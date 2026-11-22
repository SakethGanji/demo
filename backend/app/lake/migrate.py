"""P10 (plan §5.11, R14, §7 rows 1 and 9): migrate CSV-era dataset versions into the lake.

A CSV-era version is `data/datasets/<id>/data.csv` (+ `schema.json` from DL4, `lineage.json`, split JSONs recorded in
`split_files`). Each one becomes a Parquet version **in place** (the same `ds_` id, root, number, runs, edges), by
`migrate_version`, root by root, v1 first, as a resumable `migrate` job (`POST /api/admin/lake/migrate?dry_run=0`):

1. **Verify.** sha256(data.csv) must equal the hash stored when the version was made, and every split JSON must equal
   the hash recorded for it (`split_files`, and every run that recorded one). A mismatch is **not migrated**: it is
   reported (`lake_migrations.status = 'skipped'`, audit `dataset_migration_skipped`) and resolved through the DL3 flow
   (restore the file, or adopt it as a new version); a later run of the job picks the version up. Migration never
   launders an outside change.
2. **Read as the worker read it** (R14): `frame_io.read(bytes, schema.json)` (plain `pd.read_csv` without one), from the
   exact bytes that were hashed.
3. **`__row_id`:** `lineage.json.root_rows` when it has one entry per row; `0..n-1` for a root; otherwise fresh ids
   (`legacy.root_rows_source = "none"`, the `derived_from` edge marked `inferred`, so nothing pairs on them). Rows stay
   **in data.csv order** (`row_id_sorted = 0` when the ids are not increasing): an old run's positions are file positions.
4. **Write** with pyarrow from that frame, then the **round-trip proof**: the file read back the way `frames.read`
   reads it in `engine_v1` mode must `equals` the frame with equal dtypes and the same ids, or the version is skipped.
   `frame_io.to_csv_bytes(readback, schema)` against the original bytes is recorded as `legacy.csv_reproducible`.
5. **Hashes:** `legacy_sha256` keeps the data.csv hash; the version gets `manifest_sha256` / `data_sha256`, and
   `datasets.sha256` switches meaning (= `data_sha256`) with `storage = 'parquet'`.
6. **Evidence kept as blobs:** data.csv → `legacy_csv` (referenced by the version and by every run that trained on
   it); each split JSON → `legacy_json` + a Parquet split with `legacy_sha256` = the JSON's sha (what the old runs
   recorded as `splits_sha256`); `lineage.json`, `schema.json`, sidecars, `TOMBSTONE.json` → `legacy_json`;
   `holdout_sample.csv` → `frozen_holdout`. A tombstone becomes a manifest with no tables.
7. **One transaction** per version (datasets row, version_tables, table_files, blobs, blob_refs, splits, run_inputs
   backfill, root row-id counter, audit `dataset_migrated`). Then the folder is **renamed** to `<id>.migrated` and a
   fresh `<id>/` keeps only what the app still reads there (the lineage summary, sample sidecars, split views).
   Nothing is deleted: `cleanup` removes `.migrated` folders only after **two** `verify-all` passes since the migration
   finished (§7 row 1).

F-X2 (the X2 findings):
- **Lock (C1):** a version is migrated under `lake_migrate:lock:<id>`. CSV-era split draws and run creation hold
  `csv_write_hold` and are refused with 409 while it is held, or once the version is Parquet. The commit re-lists the
  runs and split files and starts over if they changed. It also checks that every run has its split_id and pins; after
  the commit every run must re-verify, or the version is recorded failed (`run_check`), not migrated.
- **Evidence cross-checks (M4/M5):** a split JSON is checked against `split_files.content` and every old run's
  holdout_predictions rows; the stored data hash against the version's creation audit row. A contradiction is skipped
  (`split_conflict` / `data_conflict`) for a person to resolve.
- **Kept, not dropped (M2/M3):** Feature Lab job records become legacy blobs and are copied into the fresh folder. A
  tombstone's splits come from their recorded copies, as blobs plus Parquet splits pinned by its runs.
- **No row map (M6):** the rows are matched against the parent (declared key, else row hash); matched rows keep its ids.
- **Cleanup (M1/m3):** it is refused while a migrate job runs. Per folder it needs two verify-all passes started after
  that folder's commit, every legacy blob re-hashing, and every file of the folder kept elsewhere.

The CSV-era bookkeeping that survives for versions not migrated yet (`split_files`, the DL4 split-file hashes, the
lineage row maps) lives at the end of this module (`legacy_*`): the grep gate keeps it out of everything else.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import time
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from .. import frame_io as FIO
from ..catalog import schema as S
from . import manifest as M
from .store import BlobStore

log = logging.getLogger("ensemble.migrate")

ROW_ID = "__row_id"
TABLE = "data"
MIGRATED_SUFFIX = ".migrated"
LEGACY_SPLITS_FILE = "splits.json"  # pre-F4: the split drawn on upload for the profiler's target
#: files of a version folder that the app still reads after the migration (copied into the fresh folder)
KEEP_NAMES = (".generator", "coerced.json")
#: files that are not legacy evidence (not stored as blobs)
SKIP_EVIDENCE = (".generator",)
#: DL4 (M-d): the sha256 of every CSV-era split file when it was drawn, and a compressed copy of its bytes. Read (and,
#: for versions not migrated yet, written) only by this module; created by catalog/schema.migrate_p10.
LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS split_files (
    dataset_id    TEXT NOT NULL,
    file          TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    basis         TEXT NOT NULL DEFAULT 'drawn',
    content       BLOB,
    PRIMARY KEY (dataset_id, file)
);
"""
#: the row maps a CSV-era lineage.json carries (cut out before parsing by catalog/dsets.lineage_meta)
LINEAGE_ROW_ARRAYS = (b'"root_rows":', b'"parent_rows":')
#: CSV-era tables a reset empties
LEGACY_TABLES = ("split_files",)
ARROW_TO_DUCK = {"int64": "BIGINT", "int32": "INTEGER", "double": "DOUBLE", "float": "FLOAT", "string": "VARCHAR",
                 "large_string": "VARCHAR", "bool": "BOOLEAN"}


class MigrateError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    @property
    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


class _Skip(Exception):
    def __init__(self, reason: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.reason, self.message, self.detail = reason, message, detail


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _file_sha(p: Path) -> str | None:
    try:
        return M.file_sha256(p)[0]
    except (FileNotFoundError, IsADirectoryError):
        return None


# --------------------------------------------------------------------------- the per-version migration lock (F-X2 C1)
# While a version is migrated (from its first check to its folder step), no new split may be drawn on it and no run may
# be created on it from its CSV-era files: their bytes would be listed before they exist and moved into `<id>.migrated`
# with no blob (X2 C1). The migration takes `lake_migrate:lock:<id>` (catalog_meta); every CSV-era writer (a split draw,
# a run's creation) first writes its own `lake_migrate:hold:<id>:<token>` row, then reads the lock and the version's
# storage. Each side writes before it reads, so at least one sees the other: a writer that sees the lock (or a version
# that is Parquet by now) is refused with 409; the migration waits for the holds it sees to finish. The commit
# transaction lists the runs and split files again and starts the version over if they changed (belt and braces).

LOCK_KEY = "lake_migrate:lock:"
HOLD_KEY = "lake_migrate:hold:"
HOLD_STALE_S = 900  # a hold older than this is from a process that died
LOCK_STALE_S = 3600  # a lock with no live job behind it, older than this, is from a process that died
LOCK_WAIT_S = float(os.environ.get("ENSEMBLE_MIGRATE_LOCK_WAIT_S") or 120)


def _meta_put(con, key: str, value: str) -> None:
    con.execute("INSERT INTO catalog_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value))


def _lock_live(con, vid: str) -> dict[str, Any] | None:
    r = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (LOCK_KEY + vid,)).fetchone()
    if not r:
        return None
    v = json.loads(r[0] or "{}")
    if v.get("job_id"):
        j = con.execute("SELECT status FROM jobs WHERE id = ?", (v["job_id"],)).fetchone()
        if j is not None:
            return v if j[0] in ("queued", "leased") else None
    return v if time.time() - float(v.get("t") or 0) < LOCK_STALE_S else None


@contextlib.contextmanager
def csv_write_hold(db, vid: str, what: str):
    """A CSV-era write on `vid` (a split draw, a run's creation): refused with MigrateError 409 while the version is
    being migrated, or once it is Parquet (the caller prepared it from the CSV-era files)."""
    key = f"{HOLD_KEY}{vid}:{secrets.token_hex(6)}"
    with db.connect() as con:
        _meta_put(con, key, json.dumps({"t": time.time(), "what": what, "pid": os.getpid()}))
    try:
        with db.connect() as con:
            lk = _lock_live(con, vid)
            st = con.execute("SELECT COALESCE(storage, 'csv') FROM datasets WHERE id = ?", (vid,)).fetchone()
        if lk:
            raise MigrateError(409, "version_migrating", f"{vid} is being migrated into the lake right now (job "
                               f"{lk.get('job_id') or '-'}): {what} is refused until that is done. Try again in a moment.",
                               version_id=vid)
        if st and st[0] == "parquet":
            raise MigrateError(409, "version_migrated", f"{vid} was migrated into the lake while this {what} was being prepared "
                               "from its CSV-era files: start it again (it now uses the version's Parquet table and split).",
                               version_id=vid)
        yield
    finally:
        with db.connect() as con:
            con.execute("DELETE FROM catalog_meta WHERE key = ?", (key,))


def _holds(db, vid: str) -> list[dict[str, Any]]:
    p = f"{HOLD_KEY}{vid}:"
    with db.connect() as con:
        rows = [json.loads(r[0] or "{}") for r in con.execute("SELECT value FROM catalog_meta WHERE substr(key, 1, ?) = ?",
                                                              (len(p), p))]
    return [h for h in rows if time.time() - float(h.get("t") or 0) < HOLD_STALE_S]


def _take_lock(db, vid: str, job_id: str | None) -> None:
    with db.connect() as con:
        _meta_put(con, LOCK_KEY + vid, json.dumps({"t": time.time(), "job_id": job_id, "pid": os.getpid()}))
    t0 = time.time()
    while True:
        live = _holds(db, vid)
        if not live:
            return
        if time.time() - t0 > LOCK_WAIT_S:
            _release_lock(db, vid)
            raise _Skip("busy", f"{len(live)} write(s) on {vid} ({', '.join(sorted({h.get('what') or '?' for h in live}))}) did not "
                                f"finish within {LOCK_WAIT_S:.0f} s: it is not migrated now; a later run of the job picks it up.")
        time.sleep(0.2)


def _release_lock(db, vid: str) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM catalog_meta WHERE key = ?", (LOCK_KEY + vid,))


class _Changed(Exception):
    """The runs or split files of a version changed between its check and its commit: the commit is rolled back and
    the version is migrated again from the start."""


# --------------------------------------------------------------------------- what is there


def candidates(db) -> list[dict[str, Any]]:
    """CSV-era versions, root by root (roots in creation order), v1 first."""
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT d.id, COALESCE(d.root_id, d.id) AS root, d.parent_id, d.version_no, d.name, d.state, d.created_at,"
            " (SELECT MIN(x.created_at) FROM datasets x WHERE COALESCE(x.root_id, x.id) = COALESCE(d.root_id, d.id)) AS root_at"
            " FROM datasets d WHERE COALESCE(d.storage, 'csv') = 'csv'"
            " ORDER BY root_at, root, COALESCE(d.version_no, 1 << 30), d.created_at, d.id")]
    return rows


def _rec(db, vid: str) -> dict[str, Any]:
    with db.connect() as con:
        r = con.execute("SELECT * FROM datasets WHERE id = ?", (vid,)).fetchone()
    if not r:
        raise MigrateError(404, "not_found", f"No dataset version {vid}.")
    return dict(r)


def _runs_of(db, vid: str) -> list[dict[str, Any]]:
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT id, config, result, created_at, status FROM runs WHERE dataset_id = ?"
                                             " ORDER BY created_at, id", (vid,))]
    for r in rows:
        r["config"] = json.loads(r["config"] or "{}")
        r["result"] = json.loads(r["result"] or "null") or {}
    return rows


def _run_split_file(cfg: dict[str, Any], vdir: Path) -> str | None:
    """The split file a run read (relative to the version folder), from its splits_path."""
    p = cfg.get("splits_path")
    if not p:
        return None
    p = Path(p)
    if p.parent.name == "splits":
        return f"splits/{p.name}"
    return p.name


def _run_split_sha(r: dict[str, Any]) -> str | None:
    """The split hash a CSV-era run recorded (DL4: config.splits_sha256, the result's splits_integrity)."""
    cfg, res = r["config"], r["result"]
    return cfg.get("splits_sha256") or (res.get("splits_integrity") or {}).get("splits_sha256") or cfg.get("splits_sha256_expected")


def _run_data_sha(r: dict[str, Any]) -> str | None:
    cfg, res = r["config"], r["result"]
    return cfg.get("data_sha256") or res.get("data_sha256") or cfg.get("data_sha256_expected")


def _audit_hash(db, vid: str) -> dict[str, Any] | None:
    """F-X2 M5: the data hash the version's latest creation audit row recorded (`dataset_created` /
    `dataset_version_created`, hash-chained), and whether that row's chain link holds. None when no row recorded one."""
    with db.connect() as con:
        r = con.execute("SELECT id, action, details FROM audit_log WHERE action IN ('dataset_created', 'dataset_version_created')"
                        " AND json_extract(details, '$.dataset_id') = ? ORDER BY id DESC LIMIT 1", (vid,)).fetchone()
        if not r:
            return None
        sha = (json.loads(r["details"] or "{}") or {}).get("sha256")
        if not sha:
            return None
        ok, link = S.audit_link(con, r["id"])
    return {"id": r["id"], "action": r["action"], "sha256": sha, "chain_ok": bool(ok), "chain": link}


def _pred_rows(settings, run_id: str) -> np.ndarray | None:
    """The data.csv rows a CSV-era run scored as its holdout (its holdout_predictions.csv `row`), None when it has none."""
    p = settings.runs_dir / run_id / "holdout_predictions.csv"
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, usecols=["row"])["row"].to_numpy(dtype="int64")
    except (ValueError, KeyError, OSError, pd.errors.ParserError):
        return None


def _holdout_of(sp: Any) -> tuple[np.ndarray, set[int] | None]:
    fold = np.asarray((sp or {}).get("fold") or [], dtype="int64") if isinstance(sp, dict) else np.zeros(0, dtype="int64")
    lst = {int(x) for x in sp.get("holdout") or []} if isinstance(sp, dict) and "holdout" in sp else None
    return fold, lst


def _holdout_contradictions(settings, fp: Path, runs: list[dict[str, Any]], df: pd.DataFrame | None = None
                            ) -> list[tuple[str, str]]:
    """F-X2 M4: every succeeded run that read the split file `fp` against the holdout it names. A row the run scored as
    holdout must be a holdout row of the file (fold -1, and in its `holdout` list); with the frame (`df`), every labelled
    holdout row of the file must also be one the run scored. [(run, what contradicts)]"""
    runs = [r for r in runs if r.get("status") == "succeeded"]
    if not runs:
        return []
    try:
        sp = json.loads(fp.read_bytes())
    except (OSError, ValueError):
        return [(r["id"], "a holdout, but the file does not parse") for r in runs]
    fold, lst = _holdout_of(sp)
    hold = set(np.flatnonzero(fold == -1).tolist())
    out = []
    for r in runs:
        rows = _pred_rows(settings, r["id"])
        if rows is None:
            continue
        s = set(rows.tolist())
        bad = sorted((s - hold) | ((s - lst) if lst is not None else set()))
        if bad:
            out.append((r["id"], f"{len(bad)} row(s) the file does not hold out ({', '.join(map(str, bad[:5]))})"))
            continue
        label = (r["config"] or {}).get("label")
        if df is not None and label in getattr(df, "columns", []) and len(fold) == len(df):
            want = set(np.flatnonzero((fold == -1) & df[label].notna().to_numpy()).tolist())
            miss = sorted(want - s)
            if miss:
                out.append((r["id"], f"no prediction for {len(miss)} labelled holdout row(s) of the file "
                                     f"({', '.join(map(str, miss[:5]))})"))
    return out


def inspect(db, settings, vid: str) -> dict[str, Any]:
    """Step 1 without writing: what the migration of `vid` would do, and why a version would be skipped."""
    rec = _rec(db, vid)
    d = settings.datasets_dir / vid
    out: dict[str, Any] = {"version_id": vid, "root_id": rec.get("root_id") or vid, "v": rec.get("version_no"),
                           "name": rec.get("name"), "state": rec.get("state") or "active",
                           "storage": rec.get("storage") or "csv", "folder": str(d.name)}
    if (rec.get("storage") or "csv") == "parquet":
        return {**out, "action": "done", "reason": "already in the lake"}
    stored = rec.get("legacy_sha256") or rec.get("sha256")
    if rec.get("state") == "deleted":
        tomb = d / "TOMBSTONE.json"
        if not tomb.exists():
            return {**out, "action": "skip", "reason": "tombstone_missing",
                    "message": f"{vid} is deleted but its folder has no TOMBSTONE.json."}
        t = json.loads(tomb.read_text())
        hs = t.get("holdout_sample") or {}
        hp = d / "holdout_sample.csv"
        if hs and hp.exists() and hs.get("sha256") and _file_sha(hp) != hs["sha256"]:
            return {**out, "action": "skip", "reason": "holdout_sample_changed",
                    "message": f"holdout_sample.csv of {vid} differs from the hash in its tombstone.",
                    "frozen": {"stored": hs["sha256"], "now": _file_sha(hp)}}
        return {**out, "action": "tombstone", "reason": "deleted: a manifest with no tables",
                "data": {"stored": stored, "now": None, "file": "deleted"},
                "kept": sorted(p.name for p in d.iterdir()) if d.is_dir() else []}
    p = d / "data.csv"
    now = _file_sha(p)
    out["data"] = {"stored": stored, "now": now, "ok": bool(now and stored and now == stored)}
    out["read"] = "frame_io.read+schema.json" if (d / FIO.SCHEMA_FILE).exists() else "pd.read_csv"
    if now is None:
        return {**out, "action": "skip", "reason": "data_missing", "message": f"data.csv of {vid} is missing."}
    if not stored:
        return {**out, "action": "skip", "reason": "no_stored_hash",
                "message": f"{vid} has no stored data.csv hash: an outside change could not be told apart. "
                           "POST …/adopt-disk-file makes a hashed version from it."}
    if now != stored:
        return {**out, "action": "skip", "reason": "data_changed",
                "message": f"data.csv of {vid} differs from the hash stored when it was made ({stored[:12]}… then, "
                           f"{now[:12]}… now): it is not migrated. Restore the file (POST …/verify names it), or adopt it "
                           "as a new version (POST …/adopt-disk-file)."}
    aud = _audit_hash(db, vid)  # F-X2 M5: the hash-chained audit row that recorded the version's hash when it was made
    out["data"]["audit"] = aud
    if aud and (aud["sha256"] != stored or not aud["chain_ok"]):
        why = (f"audit row #{aud['id']} ({aud['action']}) recorded {aud['sha256'][:12]}… when it was made, the database holds "
               f"{stored[:12]}… now" if aud["sha256"] != stored else f"audit row #{aud['id']} fails its chain link ({aud['chain']})")
        return {**out, "action": "skip", "reason": "data_conflict",
                "message": f"The stored hash of {vid} contradicts its audit trail: {why}. The database was changed outside the "
                           "app; it is not migrated. Restore the file and the hash from a backup, or adopt the file as a new "
                           "version (POST …/adopt-disk-file)."}
    runs = _runs_of(db, vid)
    bad_runs = [r["id"] for r in runs if _run_data_sha(r) and _run_data_sha(r) != stored and r["status"] == "succeeded"
                and (r["result"].get("data_integrity") or {}).get("match") is not False]
    splits, problems, conflicts = [], [], []
    rec_hashes = legacy_split_hashes(db, vid)
    copies = legacy_split_copies(db, vid)
    # the per-target files first (what runs since F4 name in splits_path): when the pre-F4 upload split has the same key,
    # the Parquet split is made from the per-target file and the older file is kept as evidence (a legacy_json blob)
    files = sorted({*(f"splits/{q.name}" for q in (d / "splits").glob("*.json")), *rec_hashes,
                    *([LEGACY_SPLITS_FILE] if (d / LEGACY_SPLITS_FILE).exists() else [])},
                   key=lambda f: (f == LEGACY_SPLITS_FILE, f))
    for rel in files:
        fp = d / rel
        sha = _file_sha(fp)
        recorded = rec_hashes.get(rel)
        by_runs = sorted({s for r in runs if _run_split_file(r["config"], d) == rel and (s := _run_split_sha(r))})
        e = {"file": rel, "now": sha, "recorded": recorded, "runs_recorded": by_runs}
        if sha is None:
            e["status"] = "missing"
            problems.append(f"{rel} is recorded but missing (restore it: POST …/restore-splits)")
        elif recorded and recorded != sha:
            e["status"] = "MISMATCH"
            problems.append(f"{rel} differs from the hash recorded when it was drawn ({recorded[:12]}… then, {sha[:12]}… now)")
        elif any(s != sha for s in by_runs):
            e["status"] = "MISMATCH (runs)"
            problems.append(f"{rel} differs from the hash runs recorded ({', '.join(s[:12] + '…' for s in by_runs if s != sha)})")
        else:
            e["status"] = "ok" if (recorded or by_runs) else "unrecorded (drawn before DL4; no run recorded its hash)"
            fc = []
            # F-X2 M5: the compressed copy taken when it was drawn must be the recorded bytes, and the file's
            if rel in copies:
                csha = copies[rel]
                e["recorded_copy"] = csha
                if csha is None:
                    fc.append(f"{rel}: the copy recorded when it was drawn does not decompress")
                elif recorded and csha != recorded:
                    fc.append(f"{rel}: the copy recorded when it was drawn hashes to {csha[:12]}…, not the recorded "
                              f"{recorded[:12]}… (split_files was changed outside the app)")
                elif csha != sha:
                    fc.append(f"{rel} differs from the copy recorded when it was drawn ({csha[:12]}… then, {sha[:12]}… now)")
            # F-X2 M4: every old run that read it names its holdout rows (holdout_predictions.csv): they must be its holdout
            for rid, bad in _holdout_contradictions(settings, fp, [r for r in runs if _run_split_file(r["config"], d) == rel]):
                fc.append(f"{rel}: run {rid}'s holdout_predictions.csv names {bad}")
            if fc:
                e["status"] = "CONFLICT"
                conflicts += fc
        splits.append(e)
    out["splits"] = splits
    if problems:
        return {**out, "action": "skip", "reason": "split_changed", "message": f"A split file of {vid} changed outside the "
                f"app: {'; '.join(problems)}. It is not migrated; restore it first (POST …/restore-splits)."}
    if conflicts:
        return {**out, "action": "skip", "reason": "split_conflict", "conflicts": conflicts,
                "message": f"A split file of {vid} contradicts the evidence kept of it: {'; '.join(conflicts)}. It is not "
                           "migrated, so a person can decide which is right (restore the file, POST …/restore-splits, or from a "
                           "backup); a later run of the job picks it up."}
    out["runs"] = [r["id"] for r in runs]
    if bad_runs:
        out["notes"] = [f"runs {', '.join(bad_runs)} recorded another data hash (their own gate decided)"]
    return {**out, "action": "migrate", "reason": "hashes match"}


def plan(db, settings) -> dict[str, Any]:
    """The dry run: every CSV-era version and what the job would do with it. Writes nothing."""
    rows = []
    for c in candidates(db):
        try:
            rows.append(inspect(db, settings, c["id"]))
        except Exception as e:  # noqa: BLE001 - one unreadable version does not hide the others
            rows.append({"version_id": c["id"], "root_id": c["root"], "action": "skip", "reason": "error",
                         "message": f"{type(e).__name__}: {e}"[:400]})
    with db.connect() as con:
        done = con.execute("SELECT COUNT(*) FROM datasets WHERE storage = 'parquet' AND legacy_sha256 IS NOT NULL").fetchone()[0]
    by = {}
    for r in rows:
        by[r["action"]] = by.get(r["action"], 0) + 1
    return {"dry_run": True, "versions": len(rows), "by_action": by, "already_migrated": done, "plan": rows,
            "note": "dry run: nothing was written. POST /api/admin/lake/migrate?dry_run=0 runs it as a job."}


# --------------------------------------------------------------------------- the frame, read as the worker read it


def read_as_worker(raw: bytes, schema: dict[str, Any] | None) -> pd.DataFrame:
    """R14: exactly the fit's read of a CSV-era version (frames.read(LegacyPath) → frame_io.read(bytes, schema.json))."""
    return FIO.read(raw, schema)


def _row_ids(db, settings, rec: dict[str, Any], n: int) -> tuple[np.ndarray | None, str]:
    """(ids in data.csv order, source): root_rows | root | none (fresh ids are allocated at commit time)."""
    vid = rec["id"]
    lin = legacy_lineage(settings, vid)
    rr = (lin or {}).get("root_rows")
    if isinstance(rr, list) and len(rr) == n:
        try:
            ids = np.asarray(rr, dtype="int64")
        except (TypeError, ValueError, OverflowError):
            ids = None
        if ids is not None and len(np.unique(ids)) == n:
            return ids, "root_rows"
    is_root = not rec.get("parent_id") and (rec.get("root_id") in (None, vid)) and not (lin or {}).get("parent")
    if is_root:
        return np.arange(n, dtype="int64"), "root"
    return None, "none"


def v1_frame(table: Any) -> pd.DataFrame:
    """An Arrow table (with __row_id) as `frames.read(…, mode='engine_v1')` returns it (index = __row_id)."""
    from .. import frames

    names = [c for c in table.column_names if c != ROW_ID]
    df = pd.DataFrame({c: frames._v1_column(table.column(c), c) for c in names}, columns=names)
    df.index = pd.Index(table.column(ROW_ID).to_numpy(zero_copy_only=False), name=None)
    return df


def _duck_type(t: Any) -> str:
    s = str(t)
    if s in ARROW_TO_DUCK:
        return ARROW_TO_DUCK[s]
    raise _Skip("type_unsupported", f"A column read as Arrow type {s}, which the lake does not store for a migrated version.")


def _equal(df: pd.DataFrame, back: pd.DataFrame) -> list[str]:
    """Columns whose values or dtypes differ (empty = the round trip holds)."""
    bad = []
    if list(df.columns) != list(back.columns):
        return ["(column names)"]
    a, b = df.reset_index(drop=True), back.reset_index(drop=True)
    for c in df.columns:
        if a[c].dtype != b[c].dtype or not a[c].equals(b[c]):
            bad.append(str(c))
    return bad


# --------------------------------------------------------------------------- migrate one version


class _Blobs:
    """Blobs this migration writes (put now as 'pending'; the commit makes them 'live')."""

    def __init__(self, db, store: BlobStore, tmp: Path) -> None:
        self.db, self.store, self.tmp = db, store, tmp
        self.items: dict[str, dict[str, Any]] = {}

    def put_file(self, f: Path, ext: str, kind: str, n_rows: int | None = None) -> dict[str, Any]:
        sha, nbytes = M.file_sha256(f)
        key = self.store.put_file(f, sha, ext)
        with self.db.connect() as con:
            con.execute("INSERT INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)"
                        " ON CONFLICT (sha256) DO NOTHING", (sha, key, kind, nbytes, n_rows, S.now()))
        b = {"sha256": sha, "key": key, "kind": kind, "bytes": nbytes, "n_rows": n_rows}
        self.items[sha] = b
        return b

    def put_bytes(self, raw: bytes, ext: str, kind: str, n_rows: int | None = None) -> dict[str, Any]:
        f = self.tmp / f"{secrets.token_hex(6)}.{ext}"
        f.write_bytes(raw)
        return self.put_file(f, ext, kind, n_rows)


def _split_blob(blobs: _Blobs, ids: np.ndarray, folds: np.ndarray) -> tuple[dict[str, Any], str, int]:
    """A Parquet split (__row_id BIGINT, fold TINYINT) sorted by __row_id: (blob, split_sha256, n_rows)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    order = np.argsort(ids, kind="stable")
    t = pa.table({ROW_ID: pa.array(ids[order].astype("int64")), "fold": pa.array(folds[order].astype("int8"))})
    f = blobs.tmp / f"{secrets.token_hex(6)}.split.parquet"
    pq.write_table(t, f, compression="zstd")
    ssha, n = M.split_sha256_parquet(f)
    return blobs.put_file(f, "parquet", "split", n), ssha, n


def _split_key_of(rel: str, sp: dict[str, Any]) -> str:
    from ..splits import RANDOM_STRATIFIED, split_key

    if rel.startswith("splits/"):
        return sp.get("key") or rel[len("splits/"):-len(".json")]
    return split_key(sp.get("target"), sp.get("strategy") or RANDOM_STRATIFIED)


def migrate_version(db, settings, store: BlobStore, vid: str, *, by: str, job_id: str | None = None) -> dict[str, Any]:
    """Migrate one CSV-era version (idempotent: a migrated version only gets its folder step finished)."""
    rec = _rec(db, vid)
    if (rec.get("storage") or "csv") == "parquet":
        fin = finish_folder(db, settings, vid)
        return {"version_id": vid, "status": "migrated", "already": True, **({"folder": fin} if fin else {})}
    try:
        _take_lock(db, vid, job_id)  # F-X2 C1: no new split or run on it from here to its folder step
    except _Skip as s:
        return _record_skip(db, rec, s.reason, s.message, {}, by, job_id)
    try:
        for attempt in range(3):
            ins = inspect(db, settings, vid)
            if ins["action"] == "skip":
                return _record_skip(db, rec, ins["reason"], ins.get("message") or ins["reason"], ins, by, job_id)
            tmp = store.tmp_dir("migrate-" + secrets.token_hex(4))
            try:
                if ins["action"] == "tombstone":
                    out = _migrate_tombstone(db, settings, store, rec, ins, tmp, by=by, job_id=job_id)
                else:
                    out = _migrate_live(db, settings, store, rec, ins, tmp, by=by, job_id=job_id)
                break
            except _Changed as c:
                log.warning("migration of %s: %s changed before the commit; starting it again", vid, c)
                if attempt == 2:
                    return _record_skip(db, rec, "busy", f"The runs or split files of {vid} kept changing while it was being "
                                        f"migrated ({c}); a later run of the job picks it up.", ins, by, job_id)
            except _Skip as s:
                return _record_skip(db, rec, s.reason, s.message, {**ins, **s.detail}, by, job_id)
            finally:
                rm = getattr(store, "remove_tmp", None)
                if rm:
                    rm(tmp)
        if out.get("status") == "migrated":
            out["folder"] = finish_folder(db, settings, vid)
            if not out.get("already"):
                out = _post_check(db, store, rec, out, by, job_id)
        return out
    finally:
        _release_lock(db, vid)


def _post_check(db, store: BlobStore, rec: dict[str, Any], out: dict[str, Any], by: str, job_id: str | None) -> dict[str, Any]:
    """F-X2 C1: after the commit, every run on the version re-verifies (verify_run) as it did before. A run that does not
    (its data or split hash no longer re-hashes from what it is pinned to) turns the version's record to failed
    (`run_check`): it is not reported migrated, and cleanup keeps its folder."""
    vid = rec["id"]
    checks, bad = [], []
    for r in out.get("runs") or []:
        try:
            v = verify_run(db, store, r["run_id"])
        except Exception as e:  # noqa: BLE001 - unverifiable is a failure here
            v = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        data_ok = (v.get("data") or {}).get("ok")
        split_ok = (v.get("split") or {}).get("ok")
        c = {"run_id": r["run_id"], "ok": v.get("ok"), "data": data_ok, "split": split_ok}
        checks.append(c)
        if split_ok is False or v.get("error") or (data_ok is False and r.get("data") == "legacy_csv"):
            bad.append(c)
    out["run_checks"] = checks
    if not bad:
        return out
    msg = (f"{vid} was committed but {len(bad)} of its runs do not re-verify after it: "
           + ", ".join(f"{c['run_id']} (data {c['data']}, split {c['split']})" for c in bad) + ". Its folder is kept.")
    with db.connect() as con:
        con.execute("UPDATE lake_migrations SET status = 'failed', reason = 'run_check', message = ? WHERE version_id = ?", (msg, vid))
        S.audit(con, by, "dataset_migration_skipped", {"dataset_id": vid, "root_id": rec.get("root_id") or vid, "reason": "run_check",
                                                       "message": msg[:600], "runs": bad, "job_id": job_id})
    log.error(msg)
    return {**out, "status": "failed", "reason": "run_check", "message": msg}


def _record_skip(db, rec: dict[str, Any], reason: str, message: str, detail: dict[str, Any], by: str,
                 job_id: str | None) -> dict[str, Any]:
    vid, root = rec["id"], rec.get("root_id") or rec["id"]
    with db.connect() as con:
        prev = con.execute("SELECT status, reason FROM lake_migrations WHERE version_id = ?", (vid,)).fetchone()
        con.execute("INSERT INTO lake_migrations (version_id, root_id, status, reason, message, detail, legacy_sha256, at, job_id,"
                    " attempts) VALUES (?, ?, 'skipped', ?, ?, ?, ?, ?, ?, 1) ON CONFLICT (version_id) DO UPDATE SET status = 'skipped',"
                    " reason = excluded.reason, message = excluded.message, detail = excluded.detail, at = excluded.at,"
                    " job_id = excluded.job_id, attempts = lake_migrations.attempts + 1",
                    (vid, root, reason, message, json.dumps(detail, default=str)[:20000], rec.get("legacy_sha256") or rec.get("sha256"),
                     S.now(), job_id))
        if not prev or prev["status"] != "skipped" or prev["reason"] != reason:  # audited once per reason
            S.audit(con, by, "dataset_migration_skipped", {"dataset_id": vid, "root_id": root, "reason": reason, "message": message[:600],
                                                           "data": detail.get("data"), "job_id": job_id,
                                                           "splits": [s for s in detail.get("splits") or [] if s.get("status") != "ok"]})
    log.warning("migration of %s skipped: %s", vid, message)
    return {"version_id": vid, "status": "skipped", "reason": reason, "message": message}


#: sub-folders of a version folder whose files are kept as legacy blobs and copied into the fresh folder (F-X2 M2:
#: the Feature Lab job records, `featurelab/fl_<job>.json`: the request, the search journal and the result)
KEEP_DIRS = ("featurelab",)


def _evidence(blobs: _Blobs, d: Path, *, exclude: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every JSON / CSV file of the folder (not the data and splits handled elsewhere) as a legacy blob, and every JSON
    file under its KEEP_DIRS (role `legacy_json:featurelab/<file>`)."""
    out = []
    files = sorted(p for p in d.iterdir() if not p.is_dir()) if d.is_dir() else []
    for sub in KEEP_DIRS:
        if (d / sub).is_dir():
            files += sorted(p for p in (d / sub).rglob("*") if p.is_file())
    for p in files:
        rel = p.relative_to(d).as_posix()
        if rel in exclude or p.name in SKIP_EVIDENCE:
            continue
        if p.suffix == ".json":
            b = blobs.put_file(p, "json", "legacy_json")
            out.append({"file": rel, **b, "role": f"legacy_json:{rel}"})
        elif p.name == "holdout_sample.csv":
            b = blobs.put_file(p, "csv", "frozen_holdout")
            out.append({"file": rel, **b, "role": "frozen_holdout"})
    return out


def _manifest(rec: dict[str, Any], team: str, project: str, data: dict[str, Any], legacy: dict[str, Any], by: str,
              parent_manifest: str | None, sources: list[dict[str, Any]]) -> tuple[dict[str, Any], bytes, str]:
    root = rec.get("root_id") or rec["id"]
    m = {"format": M.FORMAT,
         "dataset": {"root_id": root, "version_id": rec["id"], "version_no": rec.get("version_no"), "team_id": team,
                     "project_id": project},
         "data": data,
         "provenance": {"parent": {"version_id": rec["parent_id"], "manifest_sha256": parent_manifest} if rec.get("parent_id") else None,
                        "other_parents": [], "op": "migrate",
                        "ops": [{"op": "migrate", "from": "csv", "original_op": rec.get("op"), "by": by}],
                        "sources": sources, "checks": {"warnings": []}, "created_at": M.ts(),
                        "created_by": rec.get("created_by") or "system", "reason": "P10: CSV-era version migrated into the lake",
                        "engine": M.engine_versions()},
         "legacy": legacy}
    return M.seal(m)


def _team_project(con, rec: dict[str, Any]) -> tuple[str, str]:
    root = rec.get("root_id") or rec["id"]
    r = con.execute("SELECT team_id, project_id FROM dataset_roots WHERE id = ?", (root,)).fetchone()
    if r:
        return r["team_id"], r["project_id"]
    pid = rec.get("project_id") or S.project_id_for(root)
    return S.team_of_project(con, pid), pid


def _migrate_live(db, settings, store: BlobStore, rec: dict[str, Any], ins: dict[str, Any], tmp: Path, *, by: str,
                  job_id: str | None) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    vid = rec["id"]
    d = settings.datasets_dir / vid
    raw = (d / "data.csv").read_bytes()
    legacy = _sha(raw)
    if legacy != ins["data"]["stored"]:  # changed between the check and the read: never migrate what was not checked
        raise _Skip("data_changed", f"data.csv of {vid} changed while it was being migrated.")
    schema = FIO.load(d)
    df = read_as_worker(raw, schema)
    n = len(df)
    bad = [c for c in df.columns if str(c).startswith("__") or str(c).strip().lower() in ("_row_id", "_row")]
    if bad:
        raise _Skip("reserved_column", f"{vid} has a column named {bad[0]!r}, a name the lake reserves for the row id.")
    runs0 = _runs_of(db, vid)
    for e in ins.get("splits") or []:  # F-X2 M4: with the frame, every labelled holdout row must be one the runs scored
        bad = _holdout_contradictions(settings, d / e["file"], [r for r in runs0 if _run_split_file(r["config"], d) == e["file"]], df)
        if bad:
            raise _Skip("split_conflict", f"A split file of {vid} contradicts the evidence kept of it: "
                        + "; ".join(f"{e['file']}: run {rid}'s holdout_predictions.csv: {w}" for rid, w in bad)
                        + ". It is not migrated, so a person can decide which is right.")
    ids, source = _row_ids(db, settings, rec, n)
    fresh_from, matched = None, None
    if ids is None:  # F-X2 M6: no row map; the rows are matched against the parent (declared key, else the row hash)
        ids, fresh_from, matched = _match_parent(db, store, rec, df)
        if matched:
            source = "matched"
    blobs = _Blobs(db, store, tmp)
    # the table, in data.csv order (an old run's positions are file positions)
    frame = df.reset_index(drop=True)
    try:
        at = pa.Table.from_pandas(frame, preserve_index=False)
    except (pa.ArrowInvalid, pa.ArrowTypeError) as e:
        raise _Skip("type_unsupported", f"{vid}: a column could not be stored as one Parquet type ({str(e)[:200]}).")
    at = at.add_column(0, ROW_ID, pa.array(ids, type=pa.int64()))
    schema_cols = [{"name": f.name, "type": _duck_type(f.type), "nullable": bool(at.column(f.name).null_count)}
                   for f in at.schema if f.name != ROW_ID]
    kinds = FIO.columns(schema)
    for c in schema_cols:
        if kinds.get(c["name"]) == "text":
            c["reason"] = "schema.json: text"
    rg = config.ROW_GROUP_ROWS_WIDE if len(schema_cols) > config.WIDE_COLS else config.ROW_GROUP_ROWS
    n_parts = max(1, -(-n // config.PART_MAX_ROWS))
    per = -(-n // n_parts) if n else 0
    parts = []
    for k in range(n_parts):
        piece = at.slice(k * per, per) if n_parts > 1 else at
        f = tmp / f"part{k}.parquet"
        pq.write_table(piece, f, compression=config.PARQUET_COMPRESSION, compression_level=config.PARQUET_LEVEL,
                       row_group_size=rg)
        pids = piece.column(ROW_ID).to_numpy(zero_copy_only=False)
        b = blobs.put_file(f, "parquet", "table", len(pids))
        parts.append({**b, "role": "base", "row_id_min": int(pids.min()) if len(pids) else None,
                      "row_id_max": int(pids.max()) if len(pids) else None, "n": len(pids), "path": f})
    readback = pa.concat_tables([pq.read_table(p["path"]) for p in parts]) if len(parts) > 1 else pq.read_table(parts[0]["path"])
    back = v1_frame(readback)
    diff = _equal(df, back)
    if diff or not np.array_equal(np.asarray(back.index, dtype="int64"), ids):
        raise _Skip("frame_not_equal", f"{vid}: the Parquet read-back differs from the CSV read in {', '.join(diff[:8]) or 'row ids'}: "
                                       "not migrated.", columns=diff)
    try:
        csv_repro = FIO.to_csv_bytes(back.reset_index(drop=True), schema) == raw
    except Exception:  # noqa: BLE001 - informational only
        csv_repro = False
    sorted_ids = bool(n < 2 or (np.diff(ids) > 0).all())
    csv_blob = blobs.put_bytes(raw, "csv", "legacy_csv", n)
    # splits: each JSON as a legacy blob, and (when it covers the rows) a Parquet split carrying its sha as legacy_sha256
    from ..lake import splitstore as SS

    split_rows, split_evidence, views = [], [], []
    for e in ins.get("splits") or []:
        rel = e["file"]
        fp = d / rel
        jraw = fp.read_bytes()
        jsha = _sha(jraw)
        if jsha != e["now"]:
            raise _Skip("split_changed", f"{rel} of {vid} changed while it was being migrated.")
        jb = blobs.put_bytes(jraw, "json", "legacy_json")
        ev = {"file": rel, "sha256": jsha, "blob": jb["key"], "recorded": e.get("recorded"), "status": e["status"]}
        try:
            sp = json.loads(jraw)
        except ValueError:
            sp = {}
        fold = np.asarray(sp.get("fold") or [], dtype="int64") if isinstance(sp, dict) else np.zeros(0, dtype="int64")
        if len(fold) == n and n:
            key = _split_key_of(rel, sp)
            if any(r["split_key"] == key for r in split_rows):
                twin = next(x for x in views if x[1]["split_key"] == key)
                ev["split"] = "the same key as " + twin[0] + ": kept as evidence only"
                if np.array_equal(twin[2], fold):  # the same folds: a run that read this file trained on that split
                    ev["same_as"] = twin[1]["id"]
            else:
                sb, ssha, sn = _split_blob(blobs, ids, fold)
                meta = SS.meta_of({**sp, "adopted_from": rel})
                M.canonical(meta["params_json"])
                row = {"id": "spl_" + secrets.token_hex(6), "version_id": vid, "table_name": TABLE, "split_key": key,
                       "target": meta["target"], "strategy": meta["strategy"], "params_json": json.dumps(meta["params_json"]),
                       "seed": meta["seed"], "holdout_share": meta["holdout_share"], "n_folds": meta["n_folds"],
                       "blob_sha256": sb["sha256"], "split_sha256": ssha, "legacy_sha256": jsha, "n_rows": int(sn),
                       "n_holdout": int((fold == -1).sum()), "carried_from": None, "created_at": S.now(), "created_by": by,
                       "file": rel, "legacy_blob": jb["sha256"]}
                split_rows.append(row)
                ev["split_id"], ev["split_sha256"] = row["id"], ssha
                views.append((rel, row, fold))
        else:
            ev["split"] = f"kept as evidence only: {len(fold)} folds for {n} rows"
        split_evidence.append(ev)
    other = _evidence(blobs, d, exclude=("data.csv", LEGACY_SPLITS_FILE))
    schema_sha = _file_sha(d / FIO.SCHEMA_FILE)
    lin_sha = _file_sha(d / "lineage.json")
    table_parts = [(p["role"], p["sha256"]) for p in parts]
    tsha = M.table_sha256(table_parts)
    tman = {"name": TABLE, "table_sha256": tsha, "n_rows": n, "n_cols": len(schema_cols),
            "row_id": {"column": ROW_ID, "min": int(ids.min()) if n else None, "max": int(ids.max()) if n else None,
                       "sorted": sorted_ids},
            "schema": schema_cols, "schema_sha256": M.schema_sha256(schema_cols),
            "files": [{"sha256": p["sha256"], "key": p["key"], "role": p["role"], "bytes": p["bytes"], "n_rows": p["n"],
                       "row_id_min": p["row_id_min"], "row_id_max": p["row_id_max"]} for p in parts],
            "keys": []}
    data = {"primary_table": TABLE, "tables": [tman], "relations": []}
    leg = {"csv_sha256": legacy, "csv_blob": csv_blob["key"], "schema_json_sha256": schema_sha, "lineage_json_sha256": lin_sha,
           "root_rows_source": source, "read": ins.get("read"), "frame_equal": True, "csv_reproducible": bool(csv_repro),
           "row_order": "data.csv", "fresh_ids_from": fresh_from, **({"matched": matched} if matched else {}),
           "version_no": rec.get("version_no"),
           "created_at": rec.get("created_at"), "op": rec.get("op"),
           "splits": [{k: v for k, v in e.items() if v is not None} for e in split_evidence],
           "files": [{"file": e["file"], "sha256": e["sha256"], "kind": e["kind"]} for e in other]}
    with db.connect() as con:
        team, project = _team_project(con, rec)
        pm = con.execute("SELECT manifest_sha256 FROM datasets WHERE id = ?", (rec.get("parent_id"),)).fetchone() if rec.get("parent_id") else None
    m, mbytes, msha = _manifest(rec, team, project, data, leg, by, pm[0] if pm else None,
                                [{"filename": "data.csv", "sha256": legacy, "bytes": len(raw), "kept": True}])
    mb = blobs.put_bytes(mbytes, "json", "manifest")
    assert mb["sha256"] == msha
    dsha = m["data_sha256"]
    out = {"version_id": vid, "status": "migrated", "rows": n, "root_rows_source": source, "row_id_sorted": sorted_ids,
           "legacy_sha256": legacy, "manifest_sha256": msha, "data_sha256": dsha, "table_sha256": tsha,
           "csv_reproducible": bool(csv_repro), "splits": [{"file": r["file"], "split_id": r["id"], "legacy_sha256": r["legacy_sha256"],
                                                           "split_sha256": r["split_sha256"]} for r in split_rows],
           "read": ins.get("read")}
    runs = runs0  # listed after the check; the commit lists them again (_same_inputs)
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("SELECT storage, COALESCE(legacy_sha256, sha256) FROM datasets WHERE id = ?", (vid,)).fetchone()
        if (cur[0] or "csv") == "parquet":
            con.rollback()
            return {"version_id": vid, "status": "migrated", "already": True}
        if cur[1] != legacy:
            con.rollback()
            raise _Skip("data_changed", f"The stored hash of {vid} changed while it was being migrated.")
        _same_inputs(con, d, vid, runs, ins)  # F-X2 C1: what was checked is still all there is
        _live_blobs(con, blobs)
        con.execute("UPDATE datasets SET storage = 'parquet', manifest_sha256 = ?, data_sha256 = ?, sha256 = ?,"
                    " legacy_sha256 = COALESCE(legacy_sha256, ?), n_tables = 1, n_cols = ?, primary_table = ?, n_rows = ?"
                    " WHERE id = ?", (msha, dsha, dsha, legacy, len(schema_cols), TABLE, n, vid))
        con.execute("INSERT INTO version_tables (version_id, table_name, ordinal, table_sha256, n_rows, n_cols, bytes, schema_json,"
                    " schema_sha256, row_id_min, row_id_max, row_id_sorted, change, from_version, from_table, source_file)"
                    " VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', NULL, NULL, 'data.csv')",
                    (vid, TABLE, tsha, n, len(schema_cols), sum(p["bytes"] for p in parts), json.dumps(schema_cols),
                     M.schema_sha256(schema_cols), tman["row_id"]["min"], tman["row_id"]["max"], int(sorted_ids)))
        now = S.now()
        ref = "INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at) VALUES (?, ?, ?, ?, ?, ?)"
        for j, p in enumerate(parts):
            con.execute("INSERT INTO table_files (version_id, table_name, part, blob_sha256, role, n_rows, row_id_min, row_id_max)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (vid, TABLE, j, p["sha256"], p["role"], p["n"], p["row_id_min"], p["row_id_max"]))
            con.execute(ref, (p["sha256"], "version", vid, f"table:{TABLE}#{j}", team, now))
        con.execute(ref, (msha, "version", vid, "manifest", team, now))
        con.execute(ref, (csv_blob["sha256"], "version", vid, "legacy_csv", team, now))
        for e in other:
            con.execute(ref, (e["sha256"], "version", vid, e["role"], team, now))
        root = rec.get("root_id") or vid
        for r in split_rows:
            con.execute("INSERT INTO splits (id, version_id, table_name, split_key, target, strategy, params_json, seed, holdout_share,"
                        " n_folds, blob_sha256, split_sha256, legacy_sha256, n_rows, n_holdout, carried_from, created_at, created_by)"
                        " VALUES (:id, :version_id, :table_name, :split_key, :target, :strategy, :params_json, :seed, :holdout_share,"
                        " :n_folds, :blob_sha256, :split_sha256, :legacy_sha256, :n_rows, :n_holdout, :carried_from, :created_at,"
                        " :created_by)", {k: r[k] for k in r if k not in ("file", "legacy_blob")})
            con.execute(ref, (r["blob_sha256"], "split", r["id"], "split", team, now))
            con.execute(ref, (r["legacy_blob"], "split", r["id"], "legacy_json", team, now))
            S.audit(con, by, "split_created", {**SS.audit_details(r, root), "basis": f"migrated from {r['file']} (DL4 JSON, hash-checked)"})
        for e in split_evidence:
            if not e.get("split_id"):
                b = next(x for x in blobs.items.values() if x["key"] == e["blob"])
                con.execute(ref, (b["sha256"], "version", vid, f"legacy_json:{e['file']}", team, now))
        out["runs"] = _backfill_runs(con, vid, runs, legacy, csv_blob["sha256"], split_rows, split_evidence, d, team, now)
        bad = _check_runs(con, vid, runs, legacy, csv_blob["sha256"], split_rows, split_evidence, d)
        if bad:  # F-X2 C1: never report a version migrated with a run left unpinned
            con.rollback()
            raise _Skip("runs_unpinned", f"{vid}: {len(bad)} run(s) would be left without their evidence: {'; '.join(bad)[:800]}. "
                                         "It is not migrated.", runs_unpinned=bad)
        if n:
            con.execute("INSERT INTO dataset_root_tables (root_id, table_name, next_row_id) VALUES (?, ?, ?)"
                        " ON CONFLICT (root_id, table_name) DO UPDATE SET next_row_id = MAX(next_row_id, excluded.next_row_id)",
                        (root, TABLE, max(int(ids.max()) + 1, 0)))
        if source in ("none", "matched"):  # fresh or content-matched ids: the edge stays marked inferred
            con.execute("UPDATE lineage_edges SET inferred = 1 WHERE dst_kind = 'dataset' AND dst_id = ? AND relation = 'derived_from'", (vid,))
        _record_done(con, rec, out, job_id)
        S.audit(con, by, "dataset_migrated", {"dataset_id": vid, "root_id": root, "legacy_sha256": legacy, "manifest_sha256": msha,
                                              "data_sha256": dsha, "csv_reproducible": bool(csv_repro), "rows": n,
                                              "root_rows_source": source, "row_id_sorted": sorted_ids, "read": ins.get("read"),
                                              "splits": out["splits"], "runs": out["runs"], "job_id": job_id})
        con.commit()
    out["views"] = [(rel, row["id"]) for rel, row, _ in views]
    return out


def _split_inventory(con, d: Path, vid: str) -> tuple[dict[str, str | None], dict[str, str]]:
    """({split file: its sha256 now}, {split file: the hash recorded when it was drawn}) of a CSV-era version."""
    try:
        rec = {r[0]: r[1] for r in con.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ?", (vid,))}
    except Exception:  # noqa: BLE001 - a database without the DL4 table
        rec = {}
    names = {f"splits/{q.name}" for q in (d / "splits").glob("*.json")} | set(rec)
    if (d / LEGACY_SPLITS_FILE).exists():
        names.add(LEGACY_SPLITS_FILE)
    return {rel: _file_sha(d / rel) for rel in sorted(names)}, rec


def _same_inputs(con, d: Path, vid: str, runs: list[dict[str, Any]], ins: dict[str, Any]) -> None:
    """F-X2 C1, inside the commit transaction: the runs and split files of the version are still the ones that were
    checked and migrated. Anything new or changed rolls the commit back (_Changed: the version is started over)."""
    now_runs = [r[0] for r in con.execute("SELECT id FROM runs WHERE dataset_id = ? ORDER BY created_at, id", (vid,))]
    if now_runs != [r["id"] for r in runs]:
        con.rollback()
        raise _Changed(f"runs {', '.join(sorted(set(now_runs) ^ {r['id'] for r in runs}))}")
    files, rec = _split_inventory(con, d, vid)
    want = {e["file"]: e["now"] for e in ins.get("splits") or []}
    want_rec = {e["file"]: e["recorded"] for e in ins.get("splits") or [] if e.get("recorded")}
    if files != want or rec != want_rec:
        con.rollback()
        raise _Changed(f"split files {', '.join(sorted(k for k in set(files) | set(want) if files.get(k) != want.get(k)) or ['(recorded hashes)'])}")


def _check_runs(con, vid: str, runs: list[dict[str, Any]], legacy: str | None, csv_sha: str | None,
                split_rows: list[dict[str, Any]], split_evidence: list[dict[str, Any]], d: Path) -> list[str]:
    """F-X2 C1, inside the commit transaction after the backfill: every run on the version has its run_inputs row, its
    split_id (the Parquet split made from the file it read, or from the hash it recorded), and its pins (split blob +
    the JSON's legacy_json blob; the legacy_csv blob when it trained on these bytes). [problems]"""
    by_legacy = {r["legacy_sha256"]: r for r in split_rows}
    by_file = {r["file"]: r for r in split_rows}
    by_id = {r["id"]: r for r in split_rows}
    ev = {e["file"]: e for e in split_evidence}
    probs = []
    for r in runs:
        rid = r["id"]
        ri = con.execute("SELECT split_id, split_legacy_sha256, legacy_csv_sha256 FROM run_inputs WHERE run_id = ? AND role = 'train'",
                         (rid,)).fetchone()
        refs: dict[str, set[str]] = {}
        for x in con.execute("SELECT role, blob_sha256 FROM blob_refs WHERE owner_kind = 'run' AND owner_id = ?", (rid,)):
            refs.setdefault(x[0], set()).add(x[1])
        if ri is None:
            probs.append(f"{rid}: no run_inputs row")
            continue
        rel = _run_split_file(r["config"], d)
        ssha = ri["split_legacy_sha256"] or _run_split_sha(r)
        if rel or ssha:
            sp = by_legacy.get(ssha) if ssha else by_file.get(rel)
            if sp is None and not ssha and rel in ev and ev[rel].get("same_as"):
                sp = by_id.get(ev[rel]["same_as"])
            if sp is not None:
                if ri["split_id"] != sp["id"]:
                    probs.append(f"{rid}: split_id {ri['split_id']} is not the split made from {sp['file']} ({sp['id']})")
                elif sp["blob_sha256"] not in refs.get("split", ()) or sp["legacy_blob"] not in refs.get("legacy_json", ()):
                    probs.append(f"{rid}: the split {sp['id']} it trained on is not pinned")
            elif rel in ev and (not ssha or ssha == ev[rel]["sha256"]):
                if ev[rel]["sha256"] not in refs.get("legacy_json", ()):
                    probs.append(f"{rid}: the split file it read ({rel}) is kept as evidence only and is not pinned")
            else:
                probs.append(f"{rid}: the split it read ({rel or '-'}, recorded {str(ssha)[:12]}…) was not migrated: "
                             "no Parquet split and no blob of it")
        dsha = ri["legacy_csv_sha256"] or _run_data_sha(r)
        if legacy and dsha == legacy and csv_sha and csv_sha not in refs.get("legacy_csv", ()):
            probs.append(f"{rid}: the legacy_csv blob it trained on is not pinned")
    return probs


def _match_parent(db, store: BlobStore, rec: dict[str, Any], df: pd.DataFrame) -> tuple[np.ndarray, int | None, dict[str, Any] | None]:
    """F-X2 M6: the ids of a CSV-era version with no row map (a SQL refresh, a monitoring window), by matching its rows
    against its parent's (already in the lake: roots are migrated v1 first) with the upload matcher: the parent's
    declared key when it has one, else the exact row hash (duplicates paired in order). Matched rows keep the parent
    row's `__row_id`; the others get fresh ids. (ids in data.csv order, first fresh id, what was matched)."""
    from .writer import reserve_row_ids

    vid, root, n = rec["id"], rec.get("root_id") or rec["id"], len(df)
    ids = np.zeros(n, dtype="int64")
    have = np.zeros(n, dtype=bool)
    matched = None
    pid = rec.get("parent_id")
    if pid and n:
        try:
            with db.connect() as con:
                st = con.execute("SELECT storage FROM datasets WHERE id = ?", (pid,)).fetchone()
            if st and st[0] == "parquet":
                import pyarrow as pa

                from ..dataset_versions import Matcher
                from . import duck
                from .upload import _Parent

                p = _Parent(db, store, None, pid, None)
                method = {"method": "declared", "columns": p.declared[0]} if p.declared else {"method": "row_hash"}
                nf = df.reset_index(drop=True).copy()
                nf.insert(0, "__fr", np.arange(n, dtype="int64"))
                with duck.job() as con:
                    con.register("_mig_new", pa.Table.from_pandas(nf, preserve_index=False))
                    mt = Matcher(con, p.sql, "SELECT * FROM _mig_new", None, p.declared)
                    mt.materialise(method, "_m")
                    pairs = con.execute("SELECT __fr, pid FROM _m").fetchall()
                fr = np.asarray([a for a, _ in pairs], dtype="int64")
                pp = np.asarray([b for _, b in pairs], dtype="int64")
                if len(np.unique(fr)) == len(fr) and len(np.unique(pp)) == len(pp):
                    ids[fr], have[fr] = pp, True
                    matched = {"parent": pid, "method": method["method"], **({"columns": method["columns"]} if method.get("columns") else {}),
                               "matched": int(len(fr)), "new": int(n - len(fr)), "parent_rows": int(mt.n_parent)}
                else:
                    log.warning("migration of %s: matching against %s paired a row twice; fresh ids", vid, pid)
        except Exception as e:  # noqa: BLE001 - no match: fresh ids, as before
            log.warning("migration of %s: no match against its parent %s (%s); fresh ids", vid, pid, str(e)[:300])
            ids[:], have[:] = 0, False
            matched = None
    k = int((~have).sum())
    fresh_from = None
    if k:
        fresh_from = reserve_row_ids(db, root, TABLE, 0, k)
        ids[~have] = fresh_from + np.arange(k, dtype="int64")
    return ids, fresh_from, matched


def _live_blobs(con, blobs: _Blobs) -> None:
    shas = list(blobs.items)
    if not shas:
        return
    q = ",".join("?" * len(shas))
    con.execute(f"UPDATE blobs SET state = 'live' WHERE sha256 IN ({q}) AND state IN ('pending', 'live')", shas)
    gone = [r[0] for r in con.execute(f"SELECT sha256 FROM blobs WHERE sha256 IN ({q}) AND state <> 'live'", shas)]
    if gone:  # a blob GC was purging: never commit over it (the job re-runs this version)
        con.rollback()
        raise MigrateError(409, "blob_gone", f"Blobs {', '.join(g[:12] for g in gone)} are being purged; run the migration again.")


def _backfill_runs(con, vid: str, runs: list[dict[str, Any]], legacy: str, csv_blob: str, split_rows: list[dict[str, Any]],
                   split_evidence: list[dict[str, Any]], d: Path, team: str, now: str) -> list[dict[str, Any]]:
    """§5.11 step 8: every run on the version keeps its evidence. run_inputs rows (legacy_csv_sha256 = the run's
    data_sha256, split_legacy_sha256 = its splits_sha256) are written for runs made before P05; rows written since get
    their split_id. A run whose data hash is the legacy CSV pins the legacy_csv blob; a run whose split hash is a
    migrated split's legacy_sha256 pins that split's blob (restore-splits can offer it) and its JSON."""
    by_legacy = {r["legacy_sha256"]: r for r in split_rows}
    json_blob = {e["sha256"]: e for e in split_evidence}
    ev_by_file = {e["file"]: e for e in split_evidence}
    ref = "INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at) VALUES (?, 'run', ?, ?, ?, ?)"
    out = []
    for r in runs:
        rid, cfg = r["id"], r["config"]
        dsha = _run_data_sha(r)
        ssha = _run_split_sha(r)
        have = con.execute("SELECT id, split_id, split_legacy_sha256, legacy_csv_sha256 FROM run_inputs WHERE run_id = ? AND role = 'train'",
                           (rid,)).fetchone()
        if have:
            ssha = have["split_legacy_sha256"] or ssha
        sp = by_legacy.get(ssha) if ssha else None
        rel = _run_split_file(cfg, d)
        if not sp and not ssha:  # an old run that recorded no split hash: the file its splits_path names, as migrated
            sp = next((x for x in split_rows if x["file"] == rel), None)
            if not sp and (ev_by_file.get(rel) or {}).get("same_as"):  # F-X2: a twin file with the same folds
                sp = next((x for x in split_rows if x["id"] == ev_by_file[rel]["same_as"]), None)
        rec = {"run_id": rid, "data": "legacy_csv" if dsha == legacy else ("no hash recorded" if not dsha else "another hash"),
               "split_id": sp["id"] if sp else None}
        if have is None:
            con.execute("INSERT INTO run_inputs (id, run_id, role, version_id, table_name, legacy_csv_sha256, split_id,"
                        " split_legacy_sha256, read_mode, created_at) VALUES (?, ?, 'train', ?, ?, ?, ?, ?, 'engine_v1', ?)",
                        ("ri_" + secrets.token_hex(6), rid, vid, TABLE, dsha, sp["id"] if sp else None, ssha,
                         r.get("created_at") or now))
            rec["run_inputs"] = "backfilled"
        elif sp and not have["split_id"]:
            con.execute("UPDATE run_inputs SET split_id = ? WHERE id = ?", (sp["id"], have["id"]))
            rec["run_inputs"] = "split_id set"
        if csv_blob and (dsha == legacy or (have and have["legacy_csv_sha256"] == legacy)):
            con.execute(ref, (csv_blob, rid, "legacy_csv", team, now))
        if sp:
            con.execute(ref, (sp["blob_sha256"], rid, "split", team, now))
            con.execute(ref, (sp["legacy_blob"], rid, "legacy_json", team, now))
        elif (ssha and ssha in json_blob) or (not ssha and rel in ev_by_file):  # evidence only: its JSON is pinned
            b = json_blob[ssha] if ssha else ev_by_file[rel]
            con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                        " SELECT sha256, 'run', ?, 'legacy_json', ?, ? FROM blobs WHERE key = ?", (rid, team, now, b["blob"]))
        out.append(rec)
    return out


def _record_done(con, rec: dict[str, Any], out: dict[str, Any], job_id: str | None) -> None:
    con.execute("INSERT INTO lake_migrations (version_id, root_id, status, reason, message, detail, legacy_sha256, manifest_sha256,"
                " at, job_id, folder_state, attempts) VALUES (?, ?, 'migrated', NULL, NULL, ?, ?, ?, ?, ?, 'pending', 1)"
                " ON CONFLICT (version_id) DO UPDATE SET status = 'migrated', reason = NULL, message = NULL, detail = excluded.detail,"
                " legacy_sha256 = excluded.legacy_sha256, manifest_sha256 = excluded.manifest_sha256, at = excluded.at,"
                " job_id = excluded.job_id, folder_state = 'pending', attempts = lake_migrations.attempts + 1",
                (rec["id"], rec.get("root_id") or rec["id"], json.dumps(out, default=str)[:20000], out.get("legacy_sha256"),
                 out.get("manifest_sha256"), S.now(), job_id))


def _tombstone_splits(db, settings, blobs: _Blobs, rec: dict[str, Any], runs: list[dict[str, Any]], by: str
                      ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """F-X2 M3: the splits of a deleted version (the DL2 delete removed the files; the copy recorded when each was drawn
    is in split_files.content): each as a legacy_json blob, and, when its rows can be named (the tombstone's lineage
    row map, or 0..n-1 for a root), a Parquet split with `legacy_sha256` = the JSON's sha (what its runs recorded).
    A copy that does not re-hash to its recorded hash, or that a run's recorded hash contradicts, stops the version."""
    from ..lake import splitstore as SS

    vid = rec["id"]
    with db.connect() as con:
        try:
            rows = con.execute("SELECT file, sha256, content FROM split_files WHERE dataset_id = ? ORDER BY file", (vid,)).fetchall()
        except Exception:  # noqa: BLE001 - a database without the DL4 table
            rows = []
    lin = legacy_lineage(settings, vid) or {}
    is_root = not rec.get("parent_id") and (rec.get("root_id") in (None, vid)) and not lin.get("parent")
    split_rows, evidence = [], []
    for rel, sha, blob in rows:
        if blob is None:
            evidence.append({"file": rel, "sha256": sha, "blob": None, "recorded": sha, "status": "no copy recorded"})
            continue
        try:
            raw = zlib.decompress(blob)
        except zlib.error as e:
            raise _Skip("split_conflict", f"The recorded copy of {rel} of the deleted version {vid} does not decompress ({e}).")
        jsha = _sha(raw)
        if jsha != sha:
            raise _Skip("split_conflict", f"The recorded copy of {rel} of the deleted version {vid} hashes to {jsha[:12]}…, not the "
                                          f"recorded {sha[:12]}…: split_files was changed outside the app.")
        theirs = sorted({s for r in runs if _run_split_file(r["config"], Path(".")) == rel and (s := _run_split_sha(r)) and s != jsha})
        if theirs:
            raise _Skip("split_conflict", f"Runs on the deleted version {vid} recorded {', '.join(t[:12] + '…' for t in theirs)} for "
                                          f"{rel}; its recorded copy is {jsha[:12]}….")
        jb = blobs.put_bytes(raw, "json", "legacy_json")
        ev = {"file": rel, "sha256": jsha, "blob": jb["key"], "recorded": sha, "status": "ok (recorded copy)"}
        try:
            sp = json.loads(raw)
        except ValueError:
            sp = {}
        fold = np.asarray(sp.get("fold") or [], dtype="int64") if isinstance(sp, dict) else np.zeros(0, dtype="int64")
        n = len(fold)
        ids = legacy_row_map(lin, n) if n else None
        if ids is None and is_root and n:
            ids = np.arange(n, dtype="int64")
        key = _split_key_of(rel, sp) if isinstance(sp, dict) else None
        if ids is not None and len(np.unique(ids)) == n and key and not any(r["split_key"] == key for r in split_rows):
            sb, ssha, sn = _split_blob(blobs, ids, fold)
            meta = SS.meta_of({**sp, "adopted_from": rel})
            row = {"id": "spl_" + secrets.token_hex(6), "version_id": vid, "table_name": TABLE, "split_key": key,
                   "target": meta["target"], "strategy": meta["strategy"], "params_json": json.dumps(meta["params_json"]),
                   "seed": meta["seed"], "holdout_share": meta["holdout_share"], "n_folds": meta["n_folds"],
                   "blob_sha256": sb["sha256"], "split_sha256": ssha, "legacy_sha256": jsha, "n_rows": int(sn),
                   "n_holdout": int((fold == -1).sum()), "carried_from": None, "created_at": S.now(), "created_by": by,
                   "file": rel, "legacy_blob": jb["sha256"]}
            split_rows.append(row)
            ev["split_id"], ev["split_sha256"] = row["id"], ssha
        else:
            ev["split"] = "kept as evidence only: its rows can't be named (no row map)" if ids is None else "kept as evidence only"
        evidence.append(ev)
    return split_rows, evidence


def _migrate_tombstone(db, settings, store: BlobStore, rec: dict[str, Any], ins: dict[str, Any], tmp: Path, *, by: str,
                       job_id: str | None) -> dict[str, Any]:
    """A deleted version: its kept files (TOMBSTONE.json, lineage.json, holdout_sample.csv) as blobs, its splits from
    the copies recorded when they were drawn (F-X2 M3), and a manifest with no tables. Its runs keep their run_inputs
    (backfilled) with the hashes they recorded, pinned to their split."""
    vid = rec["id"]
    d = settings.datasets_dir / vid
    blobs = _Blobs(db, store, tmp)
    other = _evidence(blobs, d, exclude=())
    tomb = json.loads((d / "TOMBSTONE.json").read_text())
    runs = _runs_of(db, vid)
    split_rows, split_evidence = _tombstone_splits(db, settings, blobs, rec, runs, by)
    legacy = rec.get("legacy_sha256") or rec.get("sha256") or tomb.get("sha256")
    leg = {"csv_sha256": legacy, "csv_blob": None, "tombstone": True, "deleted_at": tomb.get("deleted_at"),
           "root_rows_source": None, "version_no": rec.get("version_no"), "created_at": rec.get("created_at"), "op": rec.get("op"),
           "splits": [{k: v for k, v in e.items() if v is not None} for e in split_evidence],
           "files": [{"file": e["file"], "sha256": e["sha256"], "kind": e["kind"]} for e in other]}
    data = {"primary_table": None, "tables": [], "relations": []}
    with db.connect() as con:
        team, project = _team_project(con, rec)
        pm = con.execute("SELECT manifest_sha256 FROM datasets WHERE id = ?", (rec.get("parent_id"),)).fetchone() if rec.get("parent_id") else None
    m, mbytes, msha = _manifest(rec, team, project, data, leg, by, pm[0] if pm else None, [])
    blobs.put_bytes(mbytes, "json", "manifest")
    dsha = m["data_sha256"]
    out = {"version_id": vid, "status": "migrated", "tombstone": True, "legacy_sha256": legacy, "manifest_sha256": msha,
           "data_sha256": dsha, "kept": [e["file"] for e in other],
           "splits": [{"file": r["file"], "split_id": r["id"], "legacy_sha256": r["legacy_sha256"], "split_sha256": r["split_sha256"]}
                      for r in split_rows]}
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if (con.execute("SELECT storage FROM datasets WHERE id = ?", (vid,)).fetchone()[0] or "csv") == "parquet":
            con.rollback()
            return {"version_id": vid, "status": "migrated", "already": True}
        now_runs = [r[0] for r in con.execute("SELECT id FROM runs WHERE dataset_id = ? ORDER BY created_at, id", (vid,))]
        if now_runs != [r["id"] for r in runs]:
            con.rollback()
            raise _Changed("runs")
        _live_blobs(con, blobs)
        con.execute("UPDATE datasets SET storage = 'parquet', manifest_sha256 = ?, data_sha256 = ?, sha256 = ?,"
                    " legacy_sha256 = COALESCE(legacy_sha256, ?), n_tables = 0, primary_table = NULL WHERE id = ?",
                    (msha, dsha, dsha, legacy, vid))
        now = S.now()
        ref = "INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at) VALUES (?, ?, ?, ?, ?, ?)"
        con.execute(ref, (msha, "version", vid, "manifest", team, now))
        for e in other:
            con.execute(ref, (e["sha256"], "version", vid, e["role"], team, now))
        from ..lake import splitstore as SS

        root = rec.get("root_id") or vid
        for r in split_rows:
            con.execute("INSERT INTO splits (id, version_id, table_name, split_key, target, strategy, params_json, seed, holdout_share,"
                        " n_folds, blob_sha256, split_sha256, legacy_sha256, n_rows, n_holdout, carried_from, created_at, created_by)"
                        " VALUES (:id, :version_id, :table_name, :split_key, :target, :strategy, :params_json, :seed, :holdout_share,"
                        " :n_folds, :blob_sha256, :split_sha256, :legacy_sha256, :n_rows, :n_holdout, :carried_from, :created_at,"
                        " :created_by)", {k: r[k] for k in r if k not in ("file", "legacy_blob")})
            con.execute(ref, (r["blob_sha256"], "split", r["id"], "split", team, now))
            con.execute(ref, (r["legacy_blob"], "split", r["id"], "legacy_json", team, now))
            S.audit(con, by, "split_created", {**SS.audit_details(r, root), "basis": f"migrated from the recorded copy of {r['file']} "
                                                                                       "(the version is deleted; hash-checked)"})
        for e in split_evidence:
            if not e.get("split_id") and e.get("blob"):
                con.execute(ref, (next(x["sha256"] for x in blobs.items.values() if x["key"] == e["blob"]), "version", vid,
                                  f"legacy_json:{e['file']}", team, now))
        out["runs"] = _backfill_runs(con, vid, runs, legacy, "", split_rows, [e for e in split_evidence if e.get("blob")], d, team, now)
        bad = _check_runs(con, vid, runs, None, None, split_rows, [e for e in split_evidence if e.get("blob")], d)
        if bad:
            con.rollback()
            raise _Skip("runs_unpinned", f"{vid}: {len(bad)} run(s) would be left without their split: {'; '.join(bad)[:800]}.",
                        runs_unpinned=bad)
        _record_done(con, rec, out, job_id)
        S.audit(con, by, "dataset_migrated", {"dataset_id": vid, "root_id": rec.get("root_id") or vid, "legacy_sha256": legacy,
                                              "manifest_sha256": msha, "data_sha256": dsha, "tombstone": True,
                                              "kept": out["kept"], "splits": out["splits"], "runs": out["runs"], "job_id": job_id})
        con.commit()
    return out


# --------------------------------------------------------------------------- the folder step (after the commit)


def finish_folder(db, settings, vid: str) -> dict[str, Any] | None:
    """Rename `<id>/` to `<id>.migrated` and make a fresh `<id>/` with what the app still reads there. Idempotent:
    resumes after a crash between the commit and the rename. Deletes nothing."""
    with db.connect() as con:
        r = con.execute("SELECT folder_state, detail FROM lake_migrations WHERE version_id = ? AND status = 'migrated'", (vid,)).fetchone()
    if not r or r["folder_state"] == "renamed":
        return None
    base = settings.datasets_dir
    src, dst = base / vid, base / (vid + MIGRATED_SUFFIX)
    late = []
    if src.is_dir() and not dst.exists():
        # F-X2 C1: never move a split file whose bytes are in no blob (the lock and the commit's re-check keep any from
        # appearing; if one did, its bytes are kept as a legacy_json blob of the version before the folder moves)
        late = _keep_unpreserved(db, settings, vid, src, None, only=lambda rel: rel.startswith("splits/") or rel == LEGACY_SPLITS_FILE)
    if src.is_dir() and not dst.exists():
        os.replace(src, dst)
    if dst.is_dir():
        src.mkdir(parents=True, exist_ok=True)
        for name in KEEP_NAMES:
            if (dst / name).exists() and not (src / name).exists():
                shutil.copy2(dst / name, src / name)
        _copy_keep_dirs(dst, src)
        for p in dst.glob("*.json"):  # sample sidecars and other side files the app reads by name
            if p.name in ("lineage.json", FIO.SCHEMA_FILE, LEGACY_SPLITS_FILE, "TOMBSTONE.json") or (src / p.name).exists():
                continue
            shutil.copy2(p, src / p.name)
        lin = legacy_lineage(settings, vid, folder=dst)
        if lin is not None and not (src / "lineage.json").exists():  # the summary (the rows carry __row_id now)
            (src / "lineage.json").write_text(json.dumps({k: v for k, v in lin.items() if k not in ("root_rows", "parent_rows")}))
        if (dst / "TOMBSTONE.json").exists() and not (src / "TOMBSTONE.json").exists():
            shutil.copy2(dst / "TOMBSTONE.json", src / "TOMBSTONE.json")
        _write_views(db, settings, vid, src)
        late += _keep_unpreserved(db, settings, vid, dst, src)
    state = "renamed" if dst.is_dir() else "no folder"
    with db.connect() as con:
        con.execute("UPDATE lake_migrations SET folder_state = ? WHERE version_id = ?", (state, vid))
    return {"folder": src.name, "renamed_to": dst.name if dst.is_dir() else None, "state": state,
            **({"kept_late": late} if late else {})}


def _copy_keep_dirs(dst: Path, src: Path) -> list[str]:
    """F-X2 M2: the KEEP_DIRS (Feature Lab job records) into the fresh folder, where GET …/feature-lab lists them."""
    out = []
    for sub in KEEP_DIRS:
        for p in sorted((dst / sub).rglob("*")) if (dst / sub).is_dir() else []:
            if p.is_file():
                t = src / p.relative_to(dst)
                if not t.exists():
                    t.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, t)
                    out.append(p.relative_to(dst).as_posix())
    return out


def _folder_files(folder: Path) -> list[tuple[str, Path]]:
    return [(p.relative_to(folder).as_posix(), p) for p in sorted(folder.rglob("*")) if p.is_file()]


def _keep_unpreserved(db, settings, vid: str, folder: Path, fresh: Path | None, only=None) -> list[str]:
    """Every JSON file of `folder` whose bytes are in no live blob and not byte-identical in `fresh`, put as a
    legacy_json blob referenced by the version (`legacy_json:<file>`). Idempotent. [the files kept now]"""
    from .store import open_store

    with db.connect() as con:
        live = {r[0] for r in con.execute("SELECT sha256 FROM blobs WHERE state = 'live'")}
    todo = []
    for rel, p in _folder_files(folder):
        if p.suffix != ".json" or (only and not only(rel)):
            continue
        sha = _file_sha(p)
        if sha in live or (fresh is not None and _file_sha(fresh / rel) == sha):
            continue
        todo.append((rel, p))
    if not todo:
        return []
    store = open_store(settings)
    tmp = store.tmp_dir("migrate-keep-" + secrets.token_hex(4))
    try:
        blobs = _Blobs(db, store, tmp)
        put = [(rel, blobs.put_file(p, "json", "legacy_json")) for rel, p in todo]
        with db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            _live_blobs(con, blobs)
            team, _ = _team_project(con, _rec_con(con, vid))
            for rel, b in put:
                con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                            " VALUES (?, 'version', ?, ?, ?, ?)", (b["sha256"], vid, f"legacy_json:{rel}", team, S.now()))
            S.audit(con, "system:migrate", "dataset_migration_files_kept", {"dataset_id": vid, "files": [
                {"file": rel, "sha256": b["sha256"]} for rel, b in put], "folder": folder.name})
            con.commit()
    finally:
        rm = getattr(store, "remove_tmp", None)
        if rm:
            rm(tmp)
    log.warning("migration of %s: kept %d file(s) that had no blob: %s", vid, len(put), ", ".join(r for r, _ in put))
    return [rel for rel, _ in put]


def _rec_con(con, vid: str) -> dict[str, Any]:
    r = con.execute("SELECT * FROM datasets WHERE id = ?", (vid,)).fetchone()
    return dict(r) if r else {"id": vid}


def repair_folders(db, settings) -> list[dict[str, Any]]:
    """Migrated versions whose `<id>.migrated` folder is still there (migrated before F-X2): their Feature Lab records
    copied into the fresh folder and every JSON file with no blob kept as one. Idempotent."""
    out = []
    with db.connect() as con:
        vids = [r[0] for r in con.execute("SELECT version_id FROM lake_migrations WHERE status = 'migrated' AND folder_state = 'renamed'")]
    for vid in vids:
        dst, src = settings.datasets_dir / (vid + MIGRATED_SUFFIX), settings.datasets_dir / vid
        if not dst.is_dir():
            continue
        src.mkdir(parents=True, exist_ok=True)
        copied = _copy_keep_dirs(dst, src)
        kept = _keep_unpreserved(db, settings, vid, dst, src)
        if copied or kept:
            out.append({"version_id": vid, "copied": copied, "kept": kept})
    return out


def _write_views(db, settings, vid: str, d: Path) -> None:
    """The positional split files old run configs name (`cfg.splits_path`), written as views of the Parquet splits."""
    from . import splitstore as SS
    from .store import open_store

    store = open_store(settings)
    for row in SS.rows_of_version(db, vid):
        with db.connect() as con:
            r = con.execute("SELECT detail FROM lake_migrations WHERE version_id = ?", (vid,)).fetchone()
        files = {s["split_id"]: s["file"] for s in (json.loads(r[0]) if r and r[0] else {}).get("splits") or []}
        rel = files.get(row["id"]) or f"splits/{row['split_key']}.json"
        if rel == LEGACY_SPLITS_FILE:
            rel = f"splits/{row['split_key']}.json"
        try:
            SS.write_view(d / rel, SS.view_of_row(db, store, row))
        except SS.SplitError as e:
            log.warning("view of %s: %s", row["id"], e.message)


# --------------------------------------------------------------------------- the job


PROGRESS_KEY = "lake_migrate:progress"


def _progress(db, p: dict[str, Any]) -> None:
    with db.connect() as con:
        con.execute("INSERT INTO catalog_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (PROGRESS_KEY, json.dumps(p)))


def run(db, settings, store: BlobStore, *, by: str, job_id: str | None = None, check=None, only: list[str] | None = None
        ) -> dict[str, Any]:
    """Every CSV-era version, root by root, v1 first. Per-version failures are recorded and the job goes on; a re-run
    resumes (migrated versions are not touched again, skipped ones are re-checked)."""
    todo = [c for c in candidates(db) if not only or c["id"] in only]
    with db.connect() as con:  # migrated versions whose folder step did not finish (a crash after the commit)
        pending = [r[0] for r in con.execute("SELECT version_id FROM lake_migrations WHERE status = 'migrated' AND folder_state = 'pending'")]
    p = {"job_id": job_id, "by": by, "started_at": S.now(), "total": len(todo), "done": 0, "migrated": 0, "skipped": 0,
         "failed": 0, "current": None, "status": "running"}
    _progress(db, p)
    results = []
    for vid in pending:
        finish_folder(db, settings, vid)
    try:
        repaired = repair_folders(db, settings)
    except Exception:  # noqa: BLE001 - a repair failure is logged; cleanup keeps any folder with unkept files anyway
        log.exception("repair of migrated folders failed")
        repaired = []
    p["repaired"] = repaired[:200]
    for c in todo:
        if check:
            check()
        p["current"] = c["id"]
        _progress(db, p)
        try:
            r = migrate_version(db, settings, store, c["id"], by=by, job_id=job_id)
        except Exception as e:  # noqa: BLE001 - recorded; the job goes on with the next version
            log.exception("migration of %s failed", c["id"])
            r = {"version_id": c["id"], "status": "failed", "message": f"{type(e).__name__}: {e}"[:600]}
            with db.connect() as con:
                con.execute("INSERT INTO lake_migrations (version_id, root_id, status, reason, message, at, job_id, attempts)"
                            " VALUES (?, ?, 'failed', 'error', ?, ?, ?, 1) ON CONFLICT (version_id) DO UPDATE SET status = 'failed',"
                            " reason = 'error', message = excluded.message, at = excluded.at, job_id = excluded.job_id,"
                            " attempts = lake_migrations.attempts + 1", (c["id"], c["root"], r["message"], S.now(), job_id))
        p[r["status"]] = p.get(r["status"], 0) + 1
        p["done"] += 1
        results.append({k: r.get(k) for k in ("version_id", "status", "reason", "message", "rows", "root_rows_source", "tombstone")
                        if r.get(k) is not None})
    p.update(current=None, status="finished", finished_at=S.now())
    _progress(db, p)
    with db.connect() as con:
        con.execute("INSERT INTO catalog_meta (key, value) VALUES ('lake_migrate:finished_at', ?) ON CONFLICT (key) DO UPDATE"
                    " SET value = excluded.value", (p["finished_at"],))
        left = con.execute("SELECT COUNT(*) FROM datasets WHERE COALESCE(storage, 'csv') = 'csv'").fetchone()[0]
        S.audit(con, by, "lake_migration_finished", {"job_id": job_id, **{k: p[k] for k in ("total", "migrated", "skipped", "failed")},
                                                     "csv_versions_left": left})
    return {**p, "csv_versions_left": left, "results": results}


def run_job(ctx) -> dict[str, Any]:
    """P22: the worker side of a `migrate` job (resource duck)."""
    from .store import open_store

    st = ctx.state
    settings = st.settings
    out = run(st.db, settings, open_store(settings), by=ctx.payload.get("by") or "system:migrate", job_id=ctx.job["id"],
              check=ctx.check, only=ctx.payload.get("only"))
    return {k: v for k, v in out.items() if k != "results"} | {"results": out["results"][:500]}


def status(db) -> dict[str, Any]:
    with db.connect() as con:
        p = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (PROGRESS_KEY,)).fetchone()
        rows = [dict(r) for r in con.execute("SELECT version_id, root_id, status, reason, message, legacy_sha256, manifest_sha256,"
                                             " folder_state, at, job_id, attempts FROM lake_migrations ORDER BY at")]
        left = con.execute("SELECT COUNT(*) FROM datasets WHERE COALESCE(storage, 'csv') = 'csv'").fetchone()[0]
        passes = _verify_passes(con)
    by: dict[str, int] = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    return {"progress": json.loads(p[0]) if p else None, "by_status": by, "csv_versions_left": left, "versions": rows,
            "verify_all_passes_since": passes}


# --------------------------------------------------------------------------- a run's re-verify


def verify_run(db, store: BlobStore, run_id: str) -> dict[str, Any]:
    """What a run trained on, re-hashed now against what it recorded. Data: a Parquet run's table_sha256 from its parts;
    a CSV-era run's data.csv bytes (from the legacy_csv blob once its version is migrated). Split: the pinned Parquet
    split's split_sha256, or a CSV-era run's DL4 JSON (its legacy_json blob) against the splits_sha256 it recorded."""
    from .. import frames

    with db.connect() as con:
        r = con.execute("SELECT id, dataset_id, config, result, status, created_at FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not r:
            raise MigrateError(404, "not_found", f"No run {run_id}.")
        ri = con.execute("SELECT * FROM run_inputs WHERE run_id = ? AND role = 'train'", (run_id,)).fetchone()
        refs = {x["role"]: x["blob_sha256"] for x in con.execute(
            "SELECT role, blob_sha256 FROM blob_refs WHERE owner_kind = 'run' AND owner_id = ?", (run_id,))}
    run = {"id": r["id"], "config": json.loads(r["config"] or "{}"), "result": json.loads(r["result"] or "null") or {}}
    ri = dict(ri) if ri else {}
    cfg = run["config"]
    ref = frames.resolve(cfg)
    lv = frames.legacy_version(ref)
    if ri.get("table_sha256"):
        recorded, basis = ri["table_sha256"], "table_sha256 recomputed from the Parquet parts"
    else:
        recorded = ri.get("legacy_csv_sha256") or _run_data_sha(run)
        basis = "data.csv bytes, from the version's legacy_csv blob (P10)" if lv else "data.csv bytes"
    try:
        now = frames.input_sha256(cfg)
    except Exception as e:  # noqa: BLE001 - unreadable is reported, not raised
        now, basis = None, f"{basis}: {type(e).__name__}: {e}"[:300]
    with db.connect() as con:
        st = con.execute("SELECT state FROM datasets WHERE id = ?", (r["dataset_id"],)).fetchone()
    deleted = bool(st and st[0] == "deleted")
    if deleted:  # DL2: a deleted version's data is gone on purpose; that is not a mismatch (its split is still checked)
        data = {"recorded": recorded, "now": None, "ok": None, "basis": "the version is deleted (four eyes): its data is gone; "
                "its tombstone, manifest, frozen holdout sample and splits are kept"}
    else:
        data = {"recorded": recorded, "now": now, "ok": bool(recorded and now == recorded), "basis": basis,
                "ref": frames.to_json(ref), **({"legacy_version": lv} if lv else {})}
    split: dict[str, Any] = {}
    if ri.get("split_sha256") and refs.get("split"):
        key = _blob_key(db, refs["split"])
        sh = M.split_sha256_parquet(store.local_copy(key))[0] if key and store.exists(key) else None
        split = {"recorded": ri["split_sha256"], "now": sh, "ok": sh == ri["split_sha256"], "basis": "the pinned Parquet split blob"}
    elif ri.get("split_legacy_sha256") or _run_split_sha(run):
        want = ri.get("split_legacy_sha256") or _run_split_sha(run)
        with db.connect() as con:
            b = con.execute("SELECT key FROM blobs WHERE sha256 = ?", (want,)).fetchone()
        if b:  # the DL4 JSON kept as a legacy_json blob (its key is its sha)
            now_s = _rehash_key(store, b[0])
            split = {"recorded": want, "now": now_s, "ok": now_s == want, "basis": "the DL4 split JSON, from its legacy_json blob (P10)"}
        else:
            p = Path(cfg.get("splits_path") or "")
            now_s = _file_sha(p) if p.name else None
            split = {"recorded": want, "now": now_s, "ok": now_s == want, "basis": "the DL4 split JSON file"}
    if not split:
        split = {"recorded": None, "ok": None, "basis": "this run recorded no split hash (it was made before DL4)"}
    if ri.get("split_id") and refs.get("split") and split.get("ok") is not False and not ri.get("split_sha256"):
        # a CSV-era run pinned to the Parquet split made from its JSON (P10): that split blob re-hashes too
        with db.connect() as con:
            sr = con.execute("SELECT split_sha256 FROM splits WHERE id = ?", (ri["split_id"],)).fetchone()
        key = _blob_key(db, refs["split"])
        sh = M.split_sha256_parquet(store.local_copy(key))[0] if key and store.exists(key) else None
        split["parquet"] = {"split_id": ri["split_id"], "recorded": sr[0] if sr else None, "now": sh, "ok": bool(sr and sh == sr[0])}
        if not split["parquet"]["ok"]:
            split["ok"] = False
    if deleted:
        return {"run_id": run_id, "dataset_id": r["dataset_id"], "data": data, "split": split, "read_mode": ri.get("read_mode"),
                "ok": None if split["ok"] is not False else False, "deleted": True}
    return {"run_id": run_id, "dataset_id": r["dataset_id"], "data": data, "split": split,
            "read_mode": ri.get("read_mode"), "ok": data["ok"] and split["ok"] is not False}


def run_split_sha256(cfg: dict[str, Any]) -> str | None:
    """A CSV-era run on a migrated version: the sha256 of the DL4 split JSON it read (its `splits_path` is a view of the
    Parquet split now, with other bytes). None for any other run."""
    from .. import frames

    lv = frames.legacy_version(frames.resolve(cfg))
    rel = _run_split_file(cfg, Path("."))
    if not lv or not rel:
        return None
    s = legacy_split_for_migrated(frames._db(), lv, rel)
    return (s or {}).get("legacy_sha256")


def _blob_key(db, sha: str) -> str | None:
    with db.connect() as con:
        r = con.execute("SELECT key FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
    return r[0] if r else None


# --------------------------------------------------------------------------- verify-all passes and the cleanup


VERIFY_PASSES_KEY = "verify_all:passes"


VERIFY_STARTS_KEY = "verify_all:passes_started"  # F-X2 M1: {pass end time: its start time}


def record_verify_all(db, ok: bool, at: str, started_at: str | None = None) -> None:
    """dsets.verify_all calls this: the times verify-all passed (`verify_all:passes`, a list of end times, as P24 reads
    it) and when each started (`verify_all:passes_started`). A folder is deleted by the cleanup only after two passes
    that started after its own migration commit (F-X2 M1)."""
    if not ok:
        return
    with db.connect() as con:
        r = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (VERIFY_PASSES_KEY,)).fetchone()
        passes = (json.loads(r[0]) if r else [])[-19:] + [at]
        s = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (VERIFY_STARTS_KEY,)).fetchone()
        starts = {k: v for k, v in (json.loads(s[0]) if s else {}).items() if k in passes}
        starts[at] = started_at or at
        _meta_put(con, VERIFY_PASSES_KEY, json.dumps(passes))
        _meta_put(con, VERIFY_STARTS_KEY, json.dumps(starts))


def _all_passes(con) -> list[dict[str, str]]:
    r = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (VERIFY_PASSES_KEY,)).fetchone()
    s = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (VERIFY_STARTS_KEY,)).fetchone()
    starts = json.loads(s[0]) if s else {}
    # a pass recorded before F-X2 has no start time: its end time stands in
    return [{"at": x, "started_at": starts.get(x) or x} for x in (json.loads(r[0]) if r else [])]


def _passes_after(passes: list[dict[str, str]], t: str | None) -> list[str]:
    """The passes that started after `t` (a folder's migration commit)."""
    return [p["at"] for p in passes if t and (p.get("started_at") or p["at"]) > t]


def _verify_passes(con) -> list[str]:
    """The passes since the last finished job (reported by status; the cleanup gate is per folder)."""
    fin = con.execute("SELECT value FROM catalog_meta WHERE key = 'lake_migrate:finished_at'").fetchone()
    return _passes_after(_all_passes(con), fin[0] if fin else None)


def _blob_ok(store: BlobStore, key: str, sha: str, cache: dict[str, bool]) -> bool:
    if sha not in cache:
        try:
            cache[sha] = bool(store.exists(key) and M.file_sha256(store.local_copy(key))[0] == sha)
        except Exception:  # noqa: BLE001 - unreadable = not ok
            cache[sha] = False
    return cache[sha]


def cleanup(db, settings, *, dry_run: bool = True, by: str) -> dict[str, Any]:
    """Delete `<id>.migrated` folders (§7 row 1). F-X2: per folder, only when
    - its version is recorded migrated, and no migrate job is queued or running (409 otherwise);
    - two verify-all passes started after **its own** migration commit (M1);
    - every legacy blob the version holds (legacy_csv, legacy_json:*, frozen_holdout, its splits' blobs) re-hashes (m3);
    - every file under the folder is kept elsewhere: a live blob with its bytes (re-hashed), or the same bytes in the
      fresh `<id>/` (M2: nothing is deleted that exists nowhere else)."""
    from .store import open_store

    with db.connect() as con:
        try:
            busy = [dict(r) for r in con.execute("SELECT id, status FROM jobs WHERE kind = 'migrate' AND status IN ('queued', 'leased')")]
        except Exception:  # noqa: BLE001 - no jobs table
            busy = []
        all_passes = _all_passes(con)
        passes = _verify_passes(con)
        rows = {r[0]: dict(r) for r in con.execute("SELECT version_id, status, legacy_sha256, at FROM lake_migrations")}
        bykey = {r[0]: (r[1], r[2]) for r in con.execute("SELECT sha256, key, state FROM blobs")}
    folders = sorted(p for p in settings.datasets_dir.glob("*" + MIGRATED_SUFFIX) if p.is_dir())
    store = open_store(settings)
    cache: dict[str, bool] = {}
    out = []
    for f in folders:
        vid = f.name[:-len(MIGRATED_SUFFIX)]
        r = rows.get(vid)
        why, mine, lost = None, [], []
        if not r or r["status"] != "migrated":
            why = "not recorded as migrated" + (f" ({r['status']})" if r else "")
        else:
            mine = _passes_after(all_passes, r["at"])
            with db.connect() as con:
                held = [dict(x) for x in con.execute(
                    "SELECT x.role, b.key, b.sha256, b.state FROM blob_refs x JOIN blobs b ON b.sha256 = x.blob_sha256"
                    " WHERE (x.owner_kind = 'version' AND x.owner_id = ? AND (x.role LIKE 'legacy%' OR x.role = 'frozen_holdout'))"
                    " OR (x.owner_kind = 'split' AND x.owner_id IN (SELECT id FROM splits WHERE version_id = ?))", (vid, vid))]
            badb = [h["role"] for h in held if h["state"] != "live" or not _blob_ok(store, h["key"], h["sha256"], cache)]
            if badb:
                why = f"{len(badb)} of its legacy blobs do not re-hash ({', '.join(sorted(set(badb))[:6])})"
            fresh = settings.datasets_dir / vid
            for rel, p in _folder_files(f):
                sha = _file_sha(p)
                b = bykey.get(sha)
                if b and b[1] == "live" and _blob_ok(store, b[0], sha, cache):
                    continue
                if (fresh / rel).is_file() and _file_sha(fresh / rel) == sha:
                    continue
                lost.append(rel)
            if lost and not why:
                why = f"{len(lost)} file(s) exist nowhere else ({', '.join(lost[:6])}{' …' if len(lost) > 6 else ''})"
            if not why and len(mine) < 2:
                why = f"{len(mine)} verify-all pass(es) started since its migration ({r['at']}); 2 are needed"
        out.append({"folder": f.name, "version_id": vid, "delete": why is None and not busy, "why_not": why,
                    "migrated_at": (r or {}).get("at"), "verify_all_passes": mine, **({"unkept_files": lost} if lost else {}),
                    "bytes": sum(p.stat().st_size for p in f.rglob("*") if p.is_file())})
    allowed = not busy and any(o["delete"] for o in out)
    if not dry_run:
        if busy:
            raise MigrateError(409, "migration_running", f"A migrate job is {busy[0]['status']} ({busy[0]['id']}): cleanup waits for "
                                                         "it to finish.", jobs=busy)
        if out and not allowed and all((o["why_not"] or "").endswith("2 are needed") for o in out):
            raise MigrateError(409, "verify_all_needed", "The migrated folders are deleted only after two verify-all passes that "
                                                         "started after each folder's migration (GET /api/admin/lake/verify-all).",
                               folders=[{k: o[k] for k in ("folder", "migrated_at", "verify_all_passes")} for o in out])
        for o in out:
            if o["delete"]:
                shutil.rmtree(settings.datasets_dir / o["folder"])
        with db.connect() as con:
            S.audit(con, by, "lake_migration_cleanup", {"deleted": [o["folder"] for o in out if o["delete"]],
                                                        "kept": [o for o in out if not o["delete"]][:200]})
    return {"dry_run": dry_run, "verify_all_passes_since_migration": passes, "allowed": allowed,
            **({"migration_running": busy} if busy else {}), "folders": out,
            "bytes": sum(o["bytes"] for o in out if o["delete"])}


# --------------------------------------------------------------------------- legacy helpers (the only CSV-era code)
# Versions not migrated yet (skipped, or a data dir that was never migrated) still need their DL4 split-file hashes
# and lineage row maps. Everything that touches them is here.


def legacy_lineage(settings, vid: str, folder: Path | None = None) -> dict[str, Any] | None:
    p = (folder or settings.datasets_dir / vid) / "lineage.json"
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, ValueError):
        return None


def legacy_row_map(lin: dict[str, Any] | None, n: int | None = None) -> np.ndarray | None:
    """The lineage row map of a CSV-era version (one root row per row), or None."""
    rr = (lin or {}).get("root_rows")
    if not rr or (n is not None and len(rr) != n):
        return None
    return np.asarray(rr, dtype="int64")


def legacy_has_row_map(lin: dict[str, Any] | None) -> bool:
    return bool((lin or {}).get("root_rows"))


def legacy_splits_path(settings_or_dir: Any, vid: str | None = None) -> Path:
    d = settings_or_dir if vid is None else settings_or_dir.datasets_dir / vid
    return Path(d) / LEGACY_SPLITS_FILE


def legacy_split_hashes(db, vid: str) -> dict[str, str]:
    with db.connect() as con:
        try:
            return {r[0]: r[1] for r in con.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ?", (vid,))}
        except Exception:  # noqa: BLE001 - a database without the DL4 table
            return {}


def legacy_split_copies(db, vid: str) -> dict[str, str | None]:
    """F-X2 M5: {file: sha256 of the compressed copy recorded when it was drawn (None: it does not decompress)}, for the
    split files that have a copy."""
    with db.connect() as con:
        try:
            rows = con.execute("SELECT file, content FROM split_files WHERE dataset_id = ? AND content IS NOT NULL", (vid,)).fetchall()
        except Exception:  # noqa: BLE001 - a database without the DL4 table
            return {}
    out: dict[str, str | None] = {}
    for rel, blob in rows:
        try:
            out[rel] = _sha(zlib.decompress(blob))
        except zlib.error:
            out[rel] = None
    return out


def legacy_split_rel(path: Path | str) -> str:
    p = Path(path)
    return f"splits/{p.name}" if p.parent.name == "splits" else p.name


def legacy_record_split(db, vid: str, path: Path, basis: str = "drawn", only_new: bool = False) -> None:
    """DL4 (M-d): record a CSV-era version's split file hash (and a compressed copy) when it is drawn."""
    sha = _file_sha(path)
    if not sha:
        return
    rel = legacy_split_rel(path)
    with db.connect() as con:
        if only_new and con.execute("SELECT 1 FROM split_files WHERE dataset_id = ? AND file = ?", (vid, rel)).fetchone():
            return
        con.execute("INSERT INTO split_files (dataset_id, file, sha256, recorded_at, basis, content) VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (dataset_id, file) DO UPDATE SET sha256 = excluded.sha256, recorded_at = excluded.recorded_at,"
                    " basis = excluded.basis, content = excluded.content",
                    (vid, rel, sha, S.now(), basis, zlib.compress(path.read_bytes())))
        rows = con.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ? ORDER BY file", (vid,)).fetchall()
        combined = hashlib.sha256("\n".join(f"{r[0]} {r[1]}" for r in rows).encode()).hexdigest() if rows else None
        con.execute("UPDATE datasets SET splits_sha256 = ? WHERE id = ?", (combined, vid))


def legacy_check_splits(db, settings, vid: str) -> list[dict[str, Any]]:
    """Each recorded split file of a CSV-era version: its stored sha256, the file's now, and ok / MISMATCH / missing."""
    out = []
    for rel, stored in sorted(legacy_split_hashes(db, vid).items()):
        now = _file_sha(settings.datasets_dir / vid / rel)
        out.append({"file": rel, "stored": stored, "now": now,
                    "status": "ok" if now == stored else ("file missing" if not now else "MISMATCH")})
    return out


def legacy_restore_splits(db, settings, vid: str) -> list[str]:
    """Write back every CSV-era split file whose bytes differ from the copy recorded when it was drawn (hash-checked).
    Not while the version is being migrated (F-X2 C1: a ValueError, as for any other refusal here)."""
    try:
        with csv_write_hold(db, vid, "restoring its split files"):
            return _restore_splits(db, settings, vid)
    except MigrateError as e:
        raise ValueError(e.message) from e


def _restore_splits(db, settings, vid: str) -> list[str]:
    restored = []
    with db.connect() as con:
        rows = con.execute("SELECT file, sha256, content FROM split_files WHERE dataset_id = ?", (vid,)).fetchall()
    for rel, sha, blob in rows:
        p = settings.datasets_dir / vid / rel
        if blob is None:
            continue
        raw = zlib.decompress(blob)
        if _sha(raw) != sha:
            raise ValueError(f"The recorded copy of {rel} does not match its own hash: it can't be restored from it.")
        if _file_sha(p) != sha:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
            restored.append(rel)
    return restored


def legacy_split_for_migrated(db, vid: str, rel: str) -> dict[str, Any] | None:
    """A migrated version's split that came from the DL4 JSON `rel` (its legacy sha and the legacy_json blob)."""
    with db.connect() as con:
        r = con.execute("SELECT detail FROM lake_migrations WHERE version_id = ? AND status = 'migrated'", (vid,)).fetchone()
    for s in (json.loads(r[0]) if r and r[0] else {}).get("splits") or []:
        if s.get("file") == rel:
            return s
    return None


def legacy_info(db, vid: str) -> dict[str, Any] | None:
    """The `legacy` block of a migrated version's manifest (None for a version that was never CSV)."""
    with db.connect() as con:
        r = con.execute("SELECT b.key FROM datasets d JOIN blobs b ON b.sha256 = d.manifest_sha256 WHERE d.id = ?"
                        " AND d.legacy_sha256 IS NOT NULL AND d.storage = 'parquet'", (vid,)).fetchone()
    if not r:
        return None
    from .. import config as _c
    from .store import open_store

    st = open_store(_c.load_settings())
    try:
        with st.open(r[0]) as f:
            return (json.loads(f.read()) or {}).get("legacy")
    except (OSError, ValueError):
        return None


def _rehash_key(store: BlobStore, key: str | None) -> str | None:
    if not key or not store.exists(key):
        return None
    with store.open(key) as f:
        return M.file_sha256(f)[0]


def legacy_verify(db, store: BlobStore, vid: str, lv: dict[str, Any]) -> dict[str, Any] | None:
    """POST …/verify of a migrated version, in its CSV-era terms: the data.csv hash (stored = legacy_sha256, now = its
    legacy_csv blob re-hashed) and each DL4 split file that was recorded (stored = the recorded hash, now = its
    legacy_json blob re-hashed, ok only when the Parquet split made from it also passes its five checks). `lv` is the
    lake verify of the version (parts, manifest, splits). None for a version that was not migrated."""
    leg = legacy_info(db, vid)
    if leg is None:
        return None
    b = legacy_blob(db, vid)
    now = _rehash_key(store, b["key"]) if b else None
    checks = {x["id"]: x for x in lv.get("splits") or []}
    splits = []
    for e in sorted(leg.get("splits") or [], key=lambda x: x["file"]):
        if not e.get("recorded"):
            continue
        jnow = _rehash_key(store, e.get("blob"))
        sc = checks.get(e.get("split_id")) if e.get("split_id") else None
        ok = jnow == e["recorded"] and (sc is None or sc["status"] == "ok")
        splits.append({"file": e["file"], "stored": e["recorded"], "now": jnow,
                       "status": "ok" if ok else ("file missing" if not jnow else "MISMATCH")})
    return {"legacy": {"stored": leg.get("csv_sha256"), "now": now, "blob": (b or {}).get("key"),
                       "root_rows_source": leg.get("root_rows_source"), "read": leg.get("read"),
                       "csv_reproducible": leg.get("csv_reproducible"),
                       "splits": [{k: e.get(k) for k in ("file", "sha256", "split_id", "split_sha256", "recorded")} for e in leg.get("splits") or []]},
            "splits": splits}


def legacy_blob(db, vid: str, role: str = "legacy_csv") -> dict[str, Any] | None:
    with db.connect() as con:
        r = con.execute("SELECT b.sha256, b.key, b.bytes FROM blob_refs x JOIN blobs b ON b.sha256 = x.blob_sha256"
                        " WHERE x.owner_kind = 'version' AND x.owner_id = ? AND x.role = ?", (vid, role)).fetchone()
    return dict(r) if r else None


#: how a CSV-era version's rows were mapped to its root (the words compare has always reported)
HOW_ROW_MAP, HOW_ROOT = "root_rows", "is the root"


def positional_run(db, run_id: str, version_id: str) -> bool:
    """A run whose holdout rows (holdout_predictions.csv `row`) are read positions, not `__row_id`s: it read a CSV-era
    version (before P10; its version may be migrated since). Its run_inputs row says so (no table_sha256); a run with no
    row is positional when its version is CSV-era or was migrated after the run was made."""
    with db.connect() as con:
        r = con.execute("SELECT table_sha256 FROM run_inputs WHERE run_id = ? AND role = 'train'", (run_id,)).fetchone()
        if r is not None:
            return r[0] is None
        v = con.execute("SELECT COALESCE(storage, 'csv') FROM datasets WHERE id = ?", (version_id,)).fetchone()
        if not v or v[0] == "csv":
            return True
        m = con.execute("SELECT m.at, r.created_at FROM lake_migrations m JOIN runs r ON r.id = ? WHERE m.version_id = ?"
                        " AND m.status = 'migrated'", (run_id, version_id)).fetchone()
    return bool(m and (m[1] or "") <= (m[0] or ""))


def legacy_mapping(db, version_id: str) -> tuple[np.ndarray | None, str, bool] | None:
    """For a migrated version: (root row of every row by read position, how, is the root) as compare reported it while
    the version was CSV-era: its row map, 0..n-1 for a root, None when it had no mapping. None for any other version."""
    leg = legacy_info(db, version_id)
    if not leg or leg.get("tombstone"):
        return None
    from . import splitstore as SS

    src = leg.get("root_rows_source")
    if src == "root_rows":
        return SS.table_row_ids(version_id), HOW_ROW_MAP, False
    if src == "root":
        return SS.table_row_ids(version_id), HOW_ROOT, True
    return None, "none", False


_DS_PATH = re.compile(r"(?:^|/)datasets/([A-Za-z0-9_.-]+)/data\.csv$")


def version_of_legacy_path(path: str) -> str | None:
    """`…/datasets/<id>/data.csv` → <id> (an old run config's csv_path)."""
    m = _DS_PATH.search(str(path).replace("\\", "/"))
    return m.group(1) if m and ".." not in m.group(1) else None

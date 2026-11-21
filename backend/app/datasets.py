"""Dataset storage: files under `data/datasets/<id>/`, records in SQLite.

    data/datasets/<id>/data.csv        the rows, exactly as profiled (row positions are the IDs used by splits)
    data/datasets/<id>/splits/<key>.json  holdout and folds per (target, strategy), drawn on first use (see splits.py)
    data/datasets/<id>/splits.json     legacy: the split drawn on upload for the profiler's target (pre-F4 runs use it)
    data/datasets/<id>/<sidecar>.json  samples only: ground truth (label_noise.json, signal.json)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import config
from . import frame_io as FIO
from . import frames
from .db import Database
from .profiling import profile_frame
from .samples import SAMPLES
from .samples_g3 import SAMPLES_G3  # G3 adds its sample here: samples.py stays unchanged (its fingerprint)
from .samples_g4 import SAMPLES_G4  # G4: loan applications with surname and zip (BISG)
from .samples_g5 import SAMPLES_G5  # G5: loans from customer types that behave differently
from .samples_g6 import SAMPLES_G6  # G6: accounts followed until they close or the data ends (censoring)
from .samples_g7 import SAMPLES_G7  # G7: applications with declines (outcomes only for the approved)
from .samples_g8 import SAMPLES_G8  # G8: complaints with overlapping topics (multi-label)
from . import splits as splits_mod
from .splits import RANDOM_STRATIFIED, load_splits, make_splits, save_splits, split_key, summarize

log = logging.getLogger("ensemble.datasets")


def decode_upload(content: bytes) -> tuple[str, str]:
    """A1 #15: the uploaded bytes as text and the encoding used. A BOM decides (UTF-8, UTF-16 LE/BE); UTF-16 without
    one is recognised by its NUL bytes; otherwise UTF-8, then cp1252 (Windows/bank exports), then latin-1."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if content.startswith(bom):
            try:
                return content[len(bom):].decode(enc), enc.upper() if enc != "utf-8" else enc
            except UnicodeDecodeError as e:
                raise DatasetError(f"The file starts with a {enc.upper()} marker but is not valid {enc.upper()} text.") from e
    head = content[:4096]
    if head.count(b"\x00") > len(head) // 4:
        enc = "utf-16-le" if head[1::2].count(b"\x00") > head[0::2].count(b"\x00") else "utf-16-be"
        try:
            return content.decode(enc), enc.upper()
        except UnicodeDecodeError as e:
            raise DatasetError("This looks like a binary file, not a CSV.") from e
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    ctrl = sum(1 for ch in text[:20000] if ord(ch) < 32 and ch not in "\t\r\n")
    if ctrl > max(2, len(text[:20000]) // 100):
        raise DatasetError("This looks like a binary file, not a CSV. Upload a .csv or .tsv text file.")
    return text, enc


def check_target(y: pd.Series, name: str) -> None:
    """A1 #15: refuse a target with nothing to learn, saying which case it is."""
    n = int(y.notna().sum())
    if n == 0:
        raise DatasetError(f"Column '{name}' is empty: every row is missing, so there is nothing to learn. "
                           "Pick the column that holds the outcome.")
    vals = y.dropna().unique()
    if len(vals) < 2:
        raise DatasetError(f"Column '{name}' has one value ({str(vals[0])!r}) in all {n:,} labelled rows: a model needs "
                           "at least two outcomes to learn from. Add rows with the other outcome.")


def guard_one_class(before: pd.Series | None, after: pd.Series | None, name: str | None, acknowledge: Any, *,
                    what: str = "This edit leaves") -> dict[str, Any] | None:
    """§4.4 (DLA4): an edit that leaves the target with one class (it had two or more) is blocked with
    `400 target_one_class` unless the request sends `"acknowledge": ["target_one_class"]`. Returns the warning to
    record when it was acknowledged, else None."""
    if before is None or after is None or not name:
        return None
    nb, na = int(before.dropna().nunique()), int(after.dropna().nunique())
    return guard_one_class_counts(nb, na, [str(v) for v in after.dropna().unique()[:1]], name, acknowledge, what=what)


def guard_one_class_counts(nb: int, na: int, left: list[str], name: str, acknowledge: Any, *,
                           what: str = "This edit leaves") -> dict[str, Any] | None:
    """guard_one_class from counts (P07: the edit compiler counts classes in DuckDB): `nb` / `na` distinct target values
    before / after, `left` the value that remains."""
    from .lake.values import ValueRefused

    if nb < 2 or na >= 2:
        return None
    msg = (f"{what} the target '{name}' with {'one class (' + repr(left[0]) + ')' if left else 'no labelled rows'} "
           f"(it had {nb}): no model can learn from it on this version. Send \"acknowledge\": [\"target_one_class\"] to "
           "make the version anyway.")
    acks = acknowledge if isinstance(acknowledge, list) else [acknowledge] if isinstance(acknowledge, str) else []
    if "target_one_class" not in acks:
        raise ValueRefused("target_one_class", msg, column=name, classes_before=nb, classes_after=na)
    return {"code": "target_one_class", "column": name, "message": msg.split(" Send")[0] + " (acknowledged)."}


def _actor_now() -> str:
    from .catalog import governance as G

    return G.current_actor() or "system"


def sniff_sep(text: str) -> str:
    """The separator: the one of , ; tab | that splits the header and the first rows into the same, largest number
    of fields (outside quotes)."""
    lines = [ln for ln in text.splitlines()[:20] if ln.strip()]
    best, best_n = ",", 1
    for sep in (",", ";", "\t", "|"):
        try:
            counts = [len(r) for r in csv.reader(lines, delimiter=sep)]
        except csv.Error:
            continue
        if not counts:
            continue
        n = counts[0]
        consistent = sum(c == n for c in counts) >= 0.8 * len(counts)
        if n > best_n and consistent:
            best, best_n = sep, n
    return best


def _generator_fingerprint(fn) -> str:
    """sha256 of the source file that defines a sample generator; changes when the generator code changes."""
    import hashlib
    import inspect
    try:
        src = inspect.getsource(inspect.getmodule(fn))
    except (OSError, TypeError):
        src = getattr(fn, "__qualname__", repr(fn))
    return hashlib.sha256(src.encode()).hexdigest()[:16]


class DatasetError(ValueError):
    """The file can't be used as a dataset; the message is shown to the user."""


def read_upload(filename: str, content: bytes) -> tuple[pd.DataFrame, str, str]:
    """The uploaded bytes as a frame, the note on how it was read, and the file's stem (A1 #15 checks, in order)."""
    if not content.strip():
        raise DatasetError("The file is empty.")
    if not re.search(r"\.(csv|tsv|txt)$", filename or "", re.I):
        raise DatasetError(f"{filename or 'This file'} is not a CSV. Upload a .csv or .tsv file with a header row.")
    text, encoding = decode_upload(content)
    sep = "\t" if filename.lower().endswith(".tsv") else sniff_sep(text)
    header = next(csv.reader(io.StringIO(text), delimiter=sep), [])
    names = [h.strip() for h in header]
    dup = sorted({h for h in names if h and names.count(h) > 1})
    if dup:
        where = "; ".join(f"'{h}' is columns {', '.join(str(i + 1) for i, n in enumerate(names) if n == h)}"
                          for h in dup)
        raise DatasetError(f"The header repeats a column name ({where}). Rename or remove the duplicate and "
                           "upload again, so no column is silently renamed.")
    from . import frames  # §6.5 R-D: the typed parse lives in frames (shared with batch files)

    df = frames.parse_text(text, sep, filename, fix_hint=f" Quote values that contain '{sep}' or fix that line, then upload again.")
    if df.shape[1] < 2:
        raise DatasetError("No separated columns found (tried comma, semicolon, tab, pipe). Give a CSV with a "
                           "header row and at least two columns.")
    if len(df) < 2:
        raise DatasetError("This file has fewer than two data rows.")
    stem = re.sub(r"\.(csv|tsv|txt)$", "", Path(filename).name, flags=re.I)
    note = "Uploaded file" + ("" if encoding in ("utf-8", "ascii") else f" (read as {encoding})") + \
        ("" if sep == "," else f" (separator {'tab' if sep == chr(9) else repr(sep)})")
    return df, note, stem


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Column names stripped and unique, numbers coerced (A1 #5), as every new dataset is written."""
    df.columns = [str(c).strip() or f"col_{i + 1}" for i, c in enumerate(df.columns)]
    if df.columns.duplicated().any():
        raise DatasetError("Column names must be unique.")
    from .profiling import coerce_numeric

    return coerce_numeric(df)[0]


#: DL3 (m3): background jobs that make versions with no person behind the request; recorded as "system:<op>"
SYSTEM_OPS = ("autopilot", "autopilot_synthetic", "sample_regenerated")
#: DL3 (m3): ops whose identical result returns the existing version instead of a duplicate
DEDUPE_OPS = ("shift_weights", "edit", "anomaly_review", "feature_lab")


class DatasetConflict(ValueError):
    """DL3: the request is valid but the data refuses it (the file on disk no longer matches its version): a 409."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DatasetStore:
    def __init__(self, settings: config.Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._split_lock = threading.Lock()
        settings.datasets_dir.mkdir(parents=True, exist_ok=True)
        from .catalog.store import Catalog

        self.catalog = Catalog(db)  # P1: dataset versions and lineage edges

    def dir(self, dataset_id: str) -> Path:
        return self.settings.datasets_dir / dataset_id

    def data_file(self, dataset_id: str) -> Path:
        """§6.5 R-D: the file this store WRITES a version to (and hashes / stats). Reads go through frames."""
        return frames.version_file(dataset_id)

    def legacy_splits_path(self, dataset_id: str) -> Path:
        return self.dir(dataset_id) / "splits.json"

    def split_path(self, dataset_id: str, target: str | None, strategy: str = RANDOM_STRATIFIED,
                   params: dict[str, Any] | None = None) -> Path:
        return self.dir(dataset_id) / "splits" / f"{split_key(target, strategy, params)}.json"

    def splits_path(self, dataset_id: str, target: str | None = None) -> Path:
        """The split file for `target` (default: the dataset's current target), drawn if it doesn't exist yet."""
        if target is None:
            rec = self.db.get_dataset(dataset_id)
            target = rec["target"] if rec else None
        return self.ensure_splits(dataset_id, target)[0]

    def ensure_splits(self, dataset_id: str, target: str | None, strategy: str = RANDOM_STRATIFIED,
                      params: dict[str, Any] | None = None) -> tuple[Path, dict[str, Any], bool]:
        """(path, splits, created) of the (target, strategy, parameters) split. An existing split is never redrawn. A
        new one is drawn from data.csv with the fixed seed, so the same file and target always give the same holdout,
        whichever target the profiler guessed. The legacy splits.json is adopted when it was drawn for this target (so
        the samples, Autopilot's derived datasets and pre-F4 runs keep their exact holdout). F6: group / time /
        time-group splits take `params` (group_column, time_column, cutoff, tuning_share); bad ones raise
        DatasetError."""
        try:
            params = splits_mod.normalize_params(strategy, params)
            path = self.split_path(dataset_id, target, strategy, params)
        except ValueError as e:
            raise DatasetError(str(e)) from e
        if frames.is_parquet(dataset_id):  # P09: a Parquet version's split is a content-addressed blob (lake/splitstore)
            return self._ensure_lake_split(dataset_id, target, strategy, params, path)
        with self._split_lock:
            if path.exists():
                self.record_split_file(dataset_id, path, "first seen (drawn before DL4)", only_new=True)
                return path, load_splits(path), False
            legacy = self.legacy_splits_path(dataset_id)
            sp = load_splits(legacy) if legacy.exists() and strategy == RANDOM_STRATIFIED else None
            if sp is not None and sp.get("target") == target and sp.get("strategy", RANDOM_STRATIFIED) == strategy:
                sp = {**sp, "strategy": strategy, "key": split_key(target, strategy), "adopted_from": "splits.json"}
            else:
                # P05: a random split needs only the target (a 10M-row Parquet version is not read whole for it)
                df = frames.read_version(dataset_id, None if strategy != RANDOM_STRATIFIED or target is None else [target])
                if target is not None and target not in df.columns:
                    raise DatasetError(f"Column '{target}' is not in the dataset.")
                y = df[target] if target is not None else None
                n = len(df) if target is not None or strategy != RANDOM_STRATIFIED else self.n_rows(dataset_id)
                try:
                    sp = make_splits(y, n, config.SEED, config.HOLDOUT_SHARE, config.N_FOLDS, strategy,
                                     frame=df if strategy != RANDOM_STRATIFIED else None, params=params)
                except ValueError as e:
                    raise DatasetError(str(e)) from e
            sp["created_at"] = sp.get("created_at") or now()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            save_splits(tmp, sp)
            tmp.replace(path)
            self.record_split_file(dataset_id, path, "drawn")  # DL4 (M-d)
            return path, sp, True

    def _ensure_lake_split(self, dataset_id: str, target: str | None, strategy: str, params: dict[str, Any],
                           path: Path) -> tuple[Path, dict[str, Any], bool]:
        """P09 (§5.7): the (target, strategy, parameters) split of a Parquet version as a `splits` row + Parquet blob.
        An existing row is returned as is (never redrawn; its integrity is the gate's and verify's job). A missing one is
        adopted from a positional file already there (a pre-P09 DL4 JSON, hash-checked against the hash recorded when
        it was drawn, or a caller's file for a derived version), else drawn with the fixed seed over the rows in `__row_id` order, and
        committed with its audit row in one transaction. `path` is the positional view consumers read."""
        from .lake import splitstore as SS
        from .lake.store import open_store

        lake = open_store(self.settings)
        key = split_key(target, strategy, params)
        table = SS.primary_table(self.db, dataset_id)
        by = None
        try:
            from .catalog import governance as G

            by = G.current_actor()
        except Exception:  # noqa: BLE001
            by = None
        by = by or "system:split draw"
        created = False
        with self._split_lock:
            row = SS.get_row(self.db, dataset_id, table, key)
            if row is None:
                try:
                    legacy = self.legacy_splits_path(dataset_id)
                    for cand in (path, legacy):  # a pre-P09 Parquet version's DL4 JSON (P05-P08 bridges), adopted once
                        if row is None and cand.exists():
                            sp0 = load_splits(cand)
                            if SS.is_view(sp0) or (cand == legacy and (sp0.get("target") != target or
                                                                         sp0.get("strategy", RANDOM_STRATIFIED) != strategy)):
                                continue
                            row = SS.adopt_legacy(self.db, lake, self, dataset_id, table, key, cand, by=by)
                except SS.SplitError as e:
                    raise DatasetConflict(e.message) from e
            if row is None:
                df = frames.read_version(dataset_id, None if strategy != RANDOM_STRATIFIED or target is None else [target])
                if target is not None and target not in df.columns:
                    raise DatasetError(f"Column '{target}' is not in the dataset.")
                y = df[target].reset_index(drop=True) if target is not None else None
                n = len(df) if target is not None or strategy != RANDOM_STRATIFIED else self.n_rows(dataset_id)
                try:
                    sp = make_splits(y, n, config.SEED, config.HOLDOUT_SHARE, config.N_FOLDS, strategy,
                                     frame=df.reset_index(drop=True) if strategy != RANDOM_STRATIFIED else None, params=params)
                except ValueError as e:
                    raise DatasetError(str(e)) from e
                ids = SS.table_row_ids(dataset_id, table)  # position i of the draw is the i-th smallest __row_id
                row = SS.commit_split(self.db, lake, dataset_id, table, key, ids, np.asarray(sp["fold"]), sp, by=by)
                created = True
            try:
                sp = SS.view_of_row(self.db, lake, row)
            except SS.SplitError as e:
                raise DatasetConflict(e.message) from e
            SS.write_view(path, sp)
        return path, sp, created

    # -- DL4 (M-d): split files are hashed when drawn, like data.csv ------------------------------------------------

    def split_rel(self, dataset_id: str, path: Path | str) -> str:
        p = Path(path)
        return f"splits/{p.name}" if p.parent.name == "splits" else p.name

    def record_split_file(self, dataset_id: str, path: Path, basis: str = "drawn", only_new: bool = False) -> None:
        import zlib

        from .catalog.schema import file_sha256

        sha = file_sha256(path)
        if not sha:
            return
        rel = self.split_rel(dataset_id, path)
        with self.db.connect() as con:
            if only_new and con.execute("SELECT 1 FROM split_files WHERE dataset_id = ? AND file = ?", (dataset_id, rel)).fetchone():
                return
            con.execute("INSERT INTO split_files (dataset_id, file, sha256, recorded_at, basis, content) VALUES (?, ?, ?, ?, ?, ?)"
                        " ON CONFLICT (dataset_id, file) DO UPDATE SET sha256 = excluded.sha256, recorded_at = excluded.recorded_at,"
                        " basis = excluded.basis, content = excluded.content",
                        (dataset_id, rel, sha, now(), basis, zlib.compress(path.read_bytes())))
            self._refresh_splits_sha(con, dataset_id)

    @staticmethod
    def _refresh_splits_sha(con: Any, dataset_id: str) -> str | None:
        import hashlib

        rows = con.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ? ORDER BY file", (dataset_id,)).fetchall()
        combined = hashlib.sha256("\n".join(f"{r[0]} {r[1]}" for r in rows).encode()).hexdigest() if rows else None
        con.execute("UPDATE datasets SET splits_sha256 = ? WHERE id = ?", (combined, dataset_id))
        return combined

    def split_hashes(self, dataset_id: str) -> dict[str, str]:
        with self.db.connect() as con:
            return {r[0]: r[1] for r in con.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ?", (dataset_id,))}

    def check_splits(self, dataset_id: str) -> list[dict[str, Any]]:
        """Each recorded split file: its stored sha256, the file's now, and ok / MISMATCH / file missing."""
        from .catalog.schema import file_sha256

        out = []
        for rel, stored in sorted(self.split_hashes(dataset_id).items()):
            now_sha = file_sha256(self.dir(dataset_id) / rel)
            out.append({"file": rel, "stored": stored, "now": now_sha,
                        "status": "ok" if now_sha == stored else ("file missing" if not now_sha else "MISMATCH")})
        return out

    def restore_split_files(self, dataset_id: str) -> list[str]:
        """Write back every split file whose bytes differ from the copy recorded when it was drawn (hash-checked)."""
        import hashlib
        import zlib

        restored = []
        with self.db.connect() as con:
            rows = con.execute("SELECT file, sha256, content FROM split_files WHERE dataset_id = ?", (dataset_id,)).fetchall()
        for rel, sha, blob in rows:
            p = self.dir(dataset_id) / rel
            if blob is None:
                continue
            raw = zlib.decompress(blob)
            if hashlib.sha256(raw).hexdigest() != sha:
                raise DatasetError(f"The recorded copy of {rel} does not match its own hash: it can't be restored from it.")
            from .catalog.schema import file_sha256

            if file_sha256(p) != sha:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
                restored.append(rel)
        return restored

    def list_splits(self, dataset_id: str) -> list[dict[str, Any]]:
        """Summaries of every split stored for the dataset (one per target and strategy used so far)."""
        if frames.is_parquet(dataset_id):  # P09: the version's `splits` rows (each a Parquet blob)
            from .lake import splitstore as SS
            from .lake.store import open_store

            lake, out = open_store(self.settings), []
            for row in SS.rows_of_version(self.db, dataset_id):
                try:
                    sp = SS.view_of_row(self.db, lake, row)
                except SS.SplitError as e:
                    out.append({"key": row["split_key"], "split_id": row["id"], "error": e.message})
                    continue
                out.append({**{k: v for k, v in summarize(sp, None).items() if k != "diagram"},
                            "created_at": row["created_at"], "adopted_from": sp.get("adopted_from"),
                            "split_id": row["id"], "split_sha256": row["split_sha256"], "blob_sha256": row["blob_sha256"],
                            "table": row["table_name"], "carried_from": row["carried_from"],
                            **({"carried": sp["carried"]} if sp.get("carried") else {})})
            return out
        d = self.dir(dataset_id) / "splits"
        out = []
        for p in sorted(d.glob("*.json")) if d.exists() else []:
            sp = load_splits(p)
            out.append({**{k: v for k, v in summarize(sp, None).items() if k != "diagram"},
                        "created_at": sp.get("created_at"), "adopted_from": sp.get("adopted_from"),
                        **({"carried": sp["carried"]} if sp.get("carried") else {})})  # P06: carried by __row_id
        return out

    def split_options(self, dataset_id: str, target: str | None) -> dict[str, Any]:
        """F6: the strategies this dataset supports and why. Date columns (≥ 95 % of values read as dates, at least
        10 distinct) and entity columns (ID-like name or a high-cardinality category whose values repeat: several
        rows per customer) are offered for time and group splits; a random split on data with either gets a
        warning, because it lets the same customer, or the future, into training."""
        dates, groups = splits_mod.date_and_group_columns(frames.read_version(dataset_id), target)
        warnings = []
        if dates:
            warnings.append(f"'{dates[0]['column']}' is a date: a random split trains on rows from after the holdout's "
                            "dates, so its score does not show how the model does on the future. Use an out-of-time split.")
        if groups:
            g = groups[0]
            warnings.append(f"'{g['column']}' has {g['groups']:,} values with {g['rows_per_group']:.2f} rows each on average: "
                            "a random split puts rows of the same entity in training and holdout. Use a group split.")
        suggested = RANDOM_STRATIFIED
        if dates and groups:
            suggested = splits_mod.TIME_GROUP
        elif dates:
            suggested = splits_mod.TIME
        elif groups:
            suggested = splits_mod.GROUP
        return {"strategies": list(splits_mod.STRATEGIES), "date_columns": dates, "group_columns": groups,
                "warnings": warnings, "suggested": suggested, "holdout_share": config.HOLDOUT_SHARE,
                "tuning_share": splits_mod.DEFAULT_TUNING_SHARE,
                "suggested_params": {"group_column": groups[0]["column"] if groups else None,
                                     "time_column": dates[0]["column"] if dates else None, "cutoff": None,
                                     "tuning_share": splits_mod.DEFAULT_TUNING_SHARE}}

    def set_target(self, dataset_id: str, target: str) -> dict[str, Any]:
        """Confirm the column to predict: re-profile for it (classes, task, conflicts), draw or reuse its split, and
        record that the user confirmed it. Other targets' splits are untouched."""
        rec = self.db.get_dataset(dataset_id)
        if not rec:
            raise DatasetError(f"No dataset with id '{dataset_id}'.")
        df = frames.read_version(dataset_id)
        if target not in df.columns:
            raise DatasetError(f"Column '{target}' is not in the dataset.")
        check_target(df[target], target)
        old = rec["profile"]
        try:
            profile = profile_frame(df, target)
        except ValueError as e:
            raise DatasetError(str(e)) from e
        for k, v in old.items():  # keep what was added after profiling (lineage) and the sample's kind
            if k not in profile:
                profile[k] = v
        profile["kind"] = old.get("kind", profile["kind"])
        profile["suggested_target"] = old.get("suggested_target", profile["suggested_target"])
        _, sp, _ = self.ensure_splits(dataset_id, target)
        profile["splits"] = summarize(sp, df[target])
        profile["target_source"] = "confirmed"
        profile["target_history"] = [*old.get("target_history", []), {"target": target, "at": now()}]
        rec = {**rec, "target": target, "profile": profile}
        self.db.insert_dataset(rec)
        return rec

    def n_rows(self, dataset_id: str) -> int:
        return frames.n_rows(frames.table(dataset_id))

    def schema(self, dataset_id: str, df: pd.DataFrame | None = None) -> dict[str, Any]:
        """DL4: the version's column schema (schema.json), or, for a version made before DL4, the types its file reads as.
        P05: a Parquet version's kinds come from its committed table schema."""
        if frames.is_parquet(dataset_id):
            return {"columns": {c.name: c.kind for c in frames.columns(frames.table(dataset_id))}}
        return FIO.load(self.dir(dataset_id)) or FIO.schema_of(frames.read_version(dataset_id) if df is None else df)

    def _same_bytes(self, sha: str, root: str, schema: dict[str, Any]) -> str | None:
        """DL4 (m-e): an existing live version of this dataset with the same bytes AND the same column schema (a number cast
        to text writes the same bytes, but it is a different version)."""
        with self.db.connect() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM datasets WHERE sha256 = ? AND (id = ? OR root_id = ?) AND state IS NOT 'deleted'"
                                             " ORDER BY created_at", (sha, root, root))]
        want = FIO.columns(schema)
        for i in ids:
            try:
                if FIO.columns(self.schema(i)) == want:
                    return i
            except Exception:  # noqa: BLE001 - an unreadable file is not a duplicate
                continue
        return None

    # -- create -----------------------------------------------------------

    def _register(self, dataset_id: str, df: pd.DataFrame, *, name: str, filename: str, source: str, note: str,
                  size_bytes: int, target: str | None = None, kind: str | None = None, by: str | None = None) -> dict[str, Any]:
        try:
            profile = profile_frame(df, target)
        except ValueError as e:
            raise DatasetError(str(e)) from e
        tgt = profile["target"]
        y = df[tgt] if tgt else None
        try:
            splits = make_splits(y, len(df), config.SEED, config.HOLDOUT_SHARE, config.N_FOLDS)
        except ValueError as e:  # DL3 (m4): too few rows, in words
            raise DatasetError(str(e)) from e
        save_splits(self.legacy_splits_path(dataset_id), splits)
        for p in (self.dir(dataset_id) / "splits").glob("*.json"):  # a regenerated sample: its old splits are stale
            p.unlink()
        with self.db.connect() as con:
            con.execute("DELETE FROM split_files WHERE dataset_id = ?", (dataset_id,))
        self.record_split_file(dataset_id, self.legacy_splits_path(dataset_id), "drawn with the version")
        _, splits, _ = self.ensure_splits(dataset_id, tgt)
        profile["splits"] = summarize(splits, y)
        # "given": named on upload (or a sample's label); "guessed": the profiler's suggestion, to be confirmed.
        profile["target_source"] = "given" if target else "guessed"
        rec = {
            "id": dataset_id, "name": name, "filename": filename, "source": source, "note": note,
            "created_at": now(), "size_bytes": size_bytes, "rows": len(df), "target": tgt,
            "kind": kind or profile["kind"], "profile": profile,
        }
        self.db.insert_dataset(rec)
        self.catalog.record_root_dataset(rec, self.data_file(dataset_id), op=source, actor=by)
        with self.db.connect() as con:
            self._refresh_splits_sha(con, dataset_id)  # DL4 (M-d): stored with the version
        return rec

    def sidecar(self, dataset_id: str, name: str) -> dict[str, Any] | None:
        """A sample's ground-truth / spec file (forecast_spec.json, anomaly_truth.json, …), or None."""
        p = self.dir(dataset_id) / name
        return json.loads(p.read_text()) if p.exists() else None

    def ensure_samples(self) -> list[str]:
        """Generate the sample datasets if they are missing. Returns the IDs generated."""
        made = []
        for dataset_id, spec in {**SAMPLES, **SAMPLES_G3, **SAMPLES_G4, **SAMPLES_G5, **SAMPLES_G6, **SAMPLES_G7, **SAMPLES_G8}.items():
            # A sample made by an older generator is stale (new columns, new planted truth): regenerate it.
            # The fingerprint is the source of the module that defines the generator.
            fp = _generator_fingerprint(spec["generate"])
            stamp = self.dir(dataset_id) / ".generator"
            current = stamp.exists() and stamp.read_text().strip() == fp
            if self.db.get_dataset(dataset_id) and self.data_file(dataset_id).exists():
                if current:
                    continue
                # DL3 (m7): a sample that runs trained on (or that has versions) is never rewritten in place: the new
                # generator's rows become a new version of it, with lineage and an audit row
                if self._sample_in_use(dataset_id):
                    try:
                        made += self._sample_as_version(dataset_id, spec, fp)
                    except Exception:  # noqa: BLE001 - never fail startup over a sample; the old one stays as it was
                        log.exception("sample %s: could not write the regenerated rows as a new version", dataset_id)
                    continue
                # Stale, or made before stamps existed: regenerate (seeded, so an unchanged generator gives the same rows).
                log.info("sample %s was made by an older generator: regenerating", dataset_id)
            log.info("generating sample dataset %s", dataset_id)
            df, sidecar = spec["generate"](seed=config.SEED)
            d = self.dir(dataset_id)
            d.mkdir(parents=True, exist_ok=True)
            df.to_csv(self.data_file(dataset_id), index=False)
            (d / spec["sidecar"]).write_text(json.dumps(sidecar, indent=1))
            # Profile what was written, so row positions and dtypes match what the engine will read.
            written = frames.read_version(dataset_id)
            self._register(dataset_id, written, name=spec["name"], filename=spec["filename"], source="sample",
                           note=spec["note"], size_bytes=self.data_file(dataset_id).stat().st_size,
                           target=sidecar["label_column"], kind=spec.get("kind"))
            stamp.write_text(fp)
            made.append(dataset_id)
        return made

    def _sample_in_use(self, dataset_id: str) -> bool:
        with self.db.connect() as con:
            return bool(con.execute("SELECT 1 FROM lineage_edges WHERE src_kind = 'dataset' AND src_id = ? AND relation IN"
                                    " ('trained_on', 'derived_from') LIMIT 1", (dataset_id,)).fetchone())

    def _sample_as_version(self, dataset_id: str, spec: dict[str, Any], fp: str) -> list[str]:
        """DL3 (m7): the regenerated sample as a new version of the one in use. P07: rows are matched by the Matcher
        with an explicit method (P06), never guessed: the parent's confirmed key when it has one, else exact row hash,
        and the method is recorded."""
        import pyarrow as pa

        from .dataset_versions import Matcher
        from .lake import duck
        from .lake.derive import ParentRows
        from .lake.store import open_store

        df, sidecar = spec["generate"](seed=config.SEED)
        df = FIO.roundtrip(df, None)  # as the engine reads it back
        lake = open_store(self.settings)
        pr = ParentRows(self.db, lake, self, dataset_id)
        p = pr.p
        method = {"method": "declared", "columns": p.declared[0]} if getattr(p, "declared", None) else {"method": "row_hash"}
        with duck.job() as con:
            p.attach(con)
            nf = df.reset_index(drop=True).copy()
            nf.insert(0, "__fr", np.arange(len(nf), dtype="int64"))
            con.register("_sample_new", pa.Table.from_pandas(nf, preserve_index=False))
            mt = Matcher(con, p.sql, "SELECT * FROM _sample_new", p.holdout_ids, getattr(p, "declared", None))
            sim = mt.simulate(method)
            mt.materialise(method, "_m")
            pairs = con.execute("SELECT __fr, pid FROM _m").fetchall()
            checks = mt.contract((self.db.get_dataset(dataset_id) or {}).get("target"))
        pos_of = {int(r): i for i, r in enumerate(pr.ids.tolist())}
        rmap = np.full(len(df), -1, dtype=int)
        for fr, pid in pairs:
            rmap[int(fr)] = pos_of.get(int(pid), -1)
        match = {"method": method["method"], **({"columns": method["columns"]} if method.get("columns") else {}),
                 "matched_by": sim.get("label"), "confirmed_by": "system:sample generator",
                 **{k: sim.get(k) for k in ("matched", "changed", "added", "removed", "holdout_identity_lost")}}
        rec = self.derive(dataset_id, df, None, "sample_regenerated",
                          [{"op": "rows_changed", "n": match["changed"]}, {"op": "rows_added", "n": match["added"]},
                           {"op": "rows_removed", "n": match["removed"]}],
                          name=spec["name"], note=f"The sample's generator changed ({fp}): its new rows, as a new version (the version "
                                                  "runs trained on is kept as it was)",
                          extra={"kind": "sample_regenerated", "generator": fp, "matched_by": match["matched_by"],
                                 "matched": match["matched"], "changed": match["changed"], "added": match["added"],
                                 "removed": match["removed"], "checks": {"warnings": checks["warnings"]}, "match": match},
                          by="system:sample generator", row_map=rmap, new_holdout_share=config.HOLDOUT_SHARE,
                          reason="the sample generator changed", dedupe=True, ops_record={"match": match})
        if not rec.get("deduplicated"):
            (self.dir(rec["id"]) / spec["sidecar"]).write_text(json.dumps(sidecar, indent=1))
        (self.dir(dataset_id) / ".generator").write_text(fp)
        log.info("sample %s is in use: the regenerated rows are version %s", dataset_id, rec["id"])
        return [rec["id"]]

    def create_version(self, parent_id: str, df: pd.DataFrame, splits: dict[str, Any], *, name: str, note: str,
                       lineage: dict[str, Any], op: str | None = None, schema: dict[str, Any] | None = None,
                       target: str | None = None) -> dict[str, Any]:
        """A new version derived from `parent_id` with the given splits. P07: a thin wrapper of derive() (every writer
        goes through the VersionWriter); `lineage.parent_rows`, when given, is the row map."""
        rm = lineage.get("parent_rows")
        return self.derive(parent_id, df, None, str(op or lineage.get("kind") or "derived"), list(lineage.get("changes") or []),
                           name=name, note=note, splits=splits,
                           extra={k: v for k, v in lineage.items() if k not in ("parent_rows", "changes")},
                           by=lineage.get("by"), row_map=rm if rm and len(rm) == len(df) else None,
                           new_rows=0 if rm else max(0, len(df) - int((self.db.get_dataset(parent_id) or {}).get("rows") or 0)),
                           reason=lineage.get("reason"), schema=schema, target=target)

    def derive(self, parent_id: str, df: pd.DataFrame, keep_idx: Any, op: str, changes: list[Any], *, name: str, note: str,
               splits: dict[str, Any] | None = None, extra: dict[str, Any] | None = None, by: str | None = None,
               new_rows: int = 0, row_map: Any = None, new_holdout_share: float = 0.0, reason: str | None = None,
               dedupe: bool | None = None, schema: dict[str, Any] | None = None, target: str | None = None,
               ops_record: dict[str, Any] | None = None) -> dict[str, Any]:
        """The one way to make a derived dataset version (P1 step 2; P07: through the VersionWriter, as Parquet).

        `df` is the new frame: the parent's rows `keep_idx` (positions in the parent as read, in order; a bool mask or
        ints; None = every row) followed by `new_rows` rows with no parent row. DL1: `row_map` (one entry per row of
        `df`: its position in the parent, or -1 for a new row) replaces keep_idx / new_rows when rows are not "kept,
        then new". A kept row keeps the parent's `__row_id`; a new row gets `next_row_id + k`. Columns the parent has
        keep the parent's type when the values fit it (lake.derive). `splits` (by position in `df`) defaults to the
        parent's split carried by `__row_id`: the holdout is the parent's holdout rows that were kept, and a seeded
        `new_holdout_share` of the new rows (splitmix64, §5.7). Only the primary table is written; the parent's other
        tables are carried. `dedupe` (default: op in DEDUPE_OPS) returns an existing version with the same data."""
        from .catalog import governance as G
        from .catalog.schema import add_edge, audit
        from .lake import derive as LD
        from .lake.store import open_store
        from .lake.writer import LakeError, VersionWriter

        parent = self.db.get_dataset(parent_id)
        if not parent:
            raise DatasetError(f"No dataset with id '{parent_id}'.")
        by = by or G.current_actor()  # DL2: the UI's "Acting as" header when the path passes no name
        if not by:  # DL3 (m3): a version needs a name; only genuine background jobs are "system:<job>", labelled as such
            if op not in SYSTEM_OPS:
                raise DatasetError(f"Say who is making this {op} version: send by (or the X-Ensemble-Actor header); "
                                   "every version records a name.")
            by = f"system:{op}"
        reason = (reason or G.current_reason() or "").strip()[:500]  # DL2: an optional reason / change ticket
        lake = open_store(self.settings)
        try:
            pr = LD.ParentRows(self.db, lake, self, parent_id)
        except LakeError as e:
            raise DatasetError(e.message) from e
        n_parent = len(pr.ids)
        df = df.reset_index(drop=True)
        df.columns = [str(c) for c in df.columns]
        if row_map is None:
            if keep_idx is None:
                keep = np.arange(n_parent)
            else:
                keep = np.asarray(keep_idx)
                keep = np.flatnonzero(keep) if keep.dtype == bool else keep.astype(int)
            if len(keep) + new_rows != len(df):
                raise DatasetError(f"derive: {len(keep)} kept + {new_rows} new rows ≠ {len(df)} rows in the frame.")
            rmap = np.concatenate([keep, np.full(new_rows, -1, dtype=int)]).astype(int)
        else:
            rmap = np.asarray(row_map, dtype=int)
            if len(rmap) != len(df) or (rmap >= n_parent).any():
                raise DatasetError(f"derive: row_map has {len(rmap)} entries for {len(df)} rows, or points past the parent's {n_parent}.")
        kept = rmap >= 0
        if len(set(rmap[kept].tolist())) != int(kept.sum()):
            raise DatasetError("derive: a parent row appears twice in the new version.")
        if pr.fresh:  # a CSV-era version with no row mapping: every row is new (unpaired)
            kept = np.zeros(len(df), dtype=bool)
        ids = np.empty(len(df), dtype="int64")
        ids[kept] = pr.ids[rmap[kept]]
        ids[~kept] = int(pr.next_row_id) + np.arange(int((~kept).sum()), dtype="int64")
        prow = np.where(kept, rmap, -1)
        order = LD.order_rows(ids)  # the table is read back in __row_id order: splits and lineage follow it
        ids_r, prow_r = ids[order], prow[order]
        given = splits  # P09: a caller's split (by position in df) is written with the version; else the parent's is carried
        if given is not None and not np.array_equal(order, np.arange(len(order))):
            given = LD.reorder_split(given, order)
        if given is not None:
            given = {**given, "derived_from": parent_id}
        hints: dict[str, str] = {}
        if schema:  # DL4 schema.json kinds of the frame (an upload's "kept as text") are honoured
            hints = {c: k for c, k in FIO.columns(schema).items() if c in df.columns}
        tgt0 = target if target is not None else parent["target"]
        tgt0 = tgt0 if tgt0 in df.columns else None
        removed = n_parent - int(kept.sum())
        lineage = {"matched_by": f"{op} (rows keep their __row_id)", **(extra or {}), "parent": parent_id, "root": pr.root,
                   "kind": (extra or {}).get("kind") or op,
                   "op": op, "rows_before": (extra or {}).get("rows_before", n_parent), "rows_after": int(len(df)),
                   "changes": changes, "by": by, **({"reason": reason} if reason else {})}
        warnings = [w for w in ((extra or {}).get("checks") or {}).get("warnings") or [] if isinstance(w, dict)] + \
            [w for w in (extra or {}).get("warnings") or [] if isinstance(w, dict)]
        w = VersionWriter(self.db, lake, op=op, by=by, parent_id=parent_id, reason=reason, name=name, primary_table=pr.table,
                          dedupe=dedupe if dedupe is not None else op in DEDUPE_OPS)
        try:
            with w:
                notes = LD.write_frame(w, pr.table, df, ids, pr.types, hints)
                LD.carry_rest(w, pr, self.db, pr.table, list(df.columns))
                split_rec = LD.split_into(w, pr, pr.table, ids_r, given, new_holdout_share,
                                          target_in_child=parent["target"] in df.columns)
                rec_op = {"op": op, "table": pr.table,
                          "rows": {"kept": int(kept.sum()), "new": int((~kept).sum()), "removed": int(removed)},
                          "ids": "fresh: the parent has no row mapping" if pr.fresh else
                                 "kept rows keep the parent's __row_id; new rows get next_row_id + k",
                          "next_row_id": int(pr.next_row_id), "n_changes": len(changes or []),
                          "changes": [c for c in (changes or [])[:50] if isinstance(c, dict)],
                          **({"types": notes} if notes else {}), **(ops_record or {}),
                          **({"split": split_rec} if split_rec else {})}
                w.extra_ops.append(LD.no_floats(rec_op))
                type_warn = [{"code": "type_changed", "column": n["column"],
                              "message": f"'{n['column']}' changed type: {n['from']} → {n['to']} (its values no longer fit {n['from']})."}
                             for n in notes if not n["kept"]]
                w.warnings = LD.no_floats([{k: v for k, v in x.items() if k not in ("values", "examples")} for x in warnings + type_warn])
                out = w.commit(make_default=False, target=tgt0, note=note)
        except LakeError as e:
            raise DatasetError(e.message) from e
        vid = out["version_id"]
        if out.get("deduped"):
            with self.db.connect() as con:
                audit(con, by, "dataset_version_deduplicated", comment=reason,
                      details={"dataset_id": vid, "root_id": pr.root, "parent": parent_id, "op": op, "data_sha256": out["data_sha256"],
                               "note": "the same data as an existing version: no new version was made, the existing one is returned"})
            rec = dict(self.db.get_dataset(vid) or {})
            rec["deduplicated"] = {"existing": vid, "data_sha256": out["data_sha256"], "sha256": out["data_sha256"]}
            return rec
        with self.db.connect() as con:
            dup = con.execute("SELECT id FROM datasets WHERE (id = ? OR root_id = ?) AND data_sha256 = ? AND id <> ?"
                              " AND state IS NOT 'deleted' ORDER BY version_no LIMIT 1", (pr.root, pr.root, out["data_sha256"], vid)).fetchone()
        if dup:
            lineage["duplicate_of"] = dup[0]
            lineage.setdefault("checks", {"warnings": []}).setdefault("warnings", []).append(
                {"code": "identical_file", "message": f"The same data as {dup[0]}: this version adds nothing but a pointer."})
        LD.lineage_summary(self, vid, lineage)  # P09: no row maps any more (the rows carry __row_id)
        rec = self.finish_lake_version(vid, target=tgt0, by=by)
        profile = rec["profile"]
        profile["target_source"] = "confirmed" if target is not None else parent["profile"].get("target_source", "given")
        profile["lineage"] = {k: v for k, v in lineage.items() if k != "changes"}
        if notes:
            profile["types_kept"] = notes
        rec = {**rec, "name": name, "note": note, "profile": profile}
        self.db.insert_dataset({k: rec[k] for k in ("id", "name", "filename", "source", "note", "created_at", "size_bytes",
                                                     "rows", "target", "kind", "profile")})
        with self.db.connect() as con:
            meta = {"op": op, "storage": "parquet",
                    **{k: lineage.get(k) for k in ("rows_before", "rows_after", "relabelled", "dropped", "synthetic", "reason",
                                                   "duplicate_of", "ops_summary") if lineage.get(k) is not None},
                    "n_changes": len(changes or [])}
            add_edge(con, ("dataset", parent_id), ("dataset", vid), "derived_from", actor=by, meta=meta, inferred=pr.fresh,
                     update_meta=True)
        return self.db.get_dataset(vid) or rec

    def lineage(self, dataset_id: str) -> dict[str, Any] | None:
        p = self.dir(dataset_id) / "lineage.json"
        return json.loads(p.read_text()) if p.exists() else None

    def create_from_upload(self, filename: str, content: Any, target: str | None = None, by: str | None = None,
                           project_id: str | None = None, lake: Any = None, size_hint: int | None = None) -> dict[str, Any]:
        """P05: `POST /datasets` as a wrapper of the staged path: the file (bytes, a path or a binary file object) is
        streamed into staging, checked (typed import, §5.1) and committed as a new Parquet dataset through the
        VersionWriter. The response keeps the old shape: the record with its full profile and the drawn split."""
        from .lake import upload as U
        from .lake.writer import LakeError

        if lake is None:
            from .lake.store import open_store

            lake = open_store(self.settings)
        who = (by or "").strip()[:80] or "unknown"
        if isinstance(content, (bytes, bytearray)):
            chunks: Any = (bytes(content[i:i + U.CHUNK]) for i in range(0, len(content), U.CHUNK))
            size_hint = len(content)
        elif isinstance(content, (str, Path)):
            fh = open(content, "rb")
            chunks = iter(lambda: fh.read(U.CHUNK), b"")
            size_hint = Path(content).stat().st_size
        else:
            chunks = iter(lambda: content.read(U.CHUNK), b"")
        plan = {"target": target} if target else {}
        try:
            u = U.stage_stream(self.db, lake, by=who, filename=Path(filename or "").name, chunks=chunks, project_id=project_id,
                               plan=plan, size_hint=size_hint)
            up = u["id"]
            try:
                chk = U.check(self.db, lake, up)
                if chk["blocks"]:
                    raise DatasetError(" ".join(b["message"] for b in chk["blocks"]))
                ver, _ = U.commit(self.db, lake, up, by=who)
            except BaseException:
                U.discard(self.db, lake, up, "the wrapper's check or commit failed")
                raise
        except LakeError as e:
            if e.status in (413, 507):
                raise
            raise DatasetError(e.message) from e
        return self.finish_lake_version(ver["version_id"], target=target, chk=chk, by=who)

    #: P05: above this many rows the legacy profile is computed on a seeded sample (exact counts patched in from DuckDB)
    PROFILE_FULL_ROWS = 1_000_000
    PROFILE_SAMPLE_ROWS = 200_000

    def finish_lake_version(self, dataset_id: str, *, target: str | None = None, chk: dict[str, Any] | None = None,
                            by: str | None = None) -> dict[str, Any]:
        """The legacy record of a new Parquet dataset (GET /datasets, POST /runs read it): profile_frame's shape, the
        target, the kind, kept-as-text notes, and (≤ PROFILE_FULL_ROWS) the drawn holdout split."""
        from . import frames

        rec = self.db.get_dataset(dataset_id)
        profile = self.lake_profile(dataset_id, target)
        tgt = profile["target"]
        t = ((chk or {}).get("tables") or [{}])[0]
        kept = list(t.get("kept_as_text") or [])
        profile["storage"] = "parquet"
        profile["tables"] = [rec.get("primary_table")] if rec.get("primary_table") else profile.get("tables", [])
        profile["schema"] = [{k: c.get(k) for k in ("name", "type", "nullable", "reason")} for c in t.get("schema") or []]
        profile["target_source"] = "given" if target else "guessed"
        if kept:
            profile["kept_as_text"] = kept
            profile.setdefault("warnings", []).append(
                f"Kept as text (codes with leading zeros, e.g. ZIP or ID codes): {', '.join(kept)}. Cast to number in Data → Edit if they are amounts.")
        for w in (chk or {}).get("warnings") or []:
            profile.setdefault("warnings", []).append(w["message"])
        n = frames.n_rows(frames.table(dataset_id))
        if n <= self.PROFILE_FULL_ROWS:
            try:
                _, sp, _ = self.ensure_splits(dataset_id, tgt)
                y = frames.read_version(dataset_id, [tgt])[tgt] if tgt else None
                profile["splits"] = summarize(sp, y)
            except DatasetError as e:  # too few rows for a split: drawn (and explained) on first use
                profile["splits_note"] = str(e)
        src = ((chk or {}).get("tables") or [{}])[0].get("source_file")
        how = [n for n in (chk or {}).get("notes") or [] if n.startswith(("read as", "separator"))]
        rec = {**rec, "target": tgt, "kind": profile["kind"], "profile": profile, "filename": src or rec["filename"],
               "note": "Uploaded file" + "".join(f" ({n})" for n in how) if chk else rec.get("note") or "Uploaded file"}
        self.db.insert_dataset({k: rec[k] for k in ("id", "name", "filename", "source", "note", "created_at", "size_bytes",
                                                     "rows", "target", "kind", "profile")})
        return self.db.get_dataset(dataset_id) or rec

    def lake_profile(self, dataset_id: str, target: str | None) -> dict[str, Any]:
        """profile_frame's shape for a Parquet version. Up to PROFILE_FULL_ROWS rows: the whole table. Above: a seeded
        reservoir sample, with rows, missing, unique (approximate), duplicates unknown and the target's classes exact."""
        from . import frames
        from .lake import duck

        ref = frames.table(dataset_id)
        lt = frames.lake_table(ref)
        if lt.n_rows <= self.PROFILE_FULL_ROWS:
            try:
                return profile_frame(frames.read(ref), target)
            except ValueError as e:
                raise DatasetError(str(e)) from e
        src = frames.lake_sql(lt)
        names = [c["name"] for c in lt.schema]
        q = lambda c: '"' + c.replace('"', '""') + '"'  # noqa: E731
        with duck.job() as con:
            sample = con.execute(f"SELECT * FROM {src} USING SAMPLE reservoir({self.PROFILE_SAMPLE_ROWS} ROWS) REPEATABLE (42)"
                                 " ORDER BY __row_id").to_arrow_table()
            exact = con.execute("SELECT " + ", ".join(f"count({q(c)}), approx_count_distinct({q(c)})" for c in names) +
                                f" FROM {src}").fetchone()
            df = pd.DataFrame({c: frames._v1_column(sample.column(c), c) for c in names}, columns=names)
            try:
                p = profile_frame(df, target)
            except ValueError as e:
                raise DatasetError(str(e)) from e
            n = lt.n_rows
            for i, col in enumerate(p["columns"]):
                nn, nu = int(exact[2 * i]), int(exact[2 * i + 1])
                col.update(missing=n - nn, missing_share=round((n - nn) / n, 6), unique=nu, unique_approximate=True)
            cells = sum(n - int(exact[2 * i]) for i in range(len(names)))
            p["rows"] = n
            p["missing"].update(cells=cells, share=round(cells / (n * len(names)), 6),
                                columns_with_missing=[c for i, c in enumerate(names) if int(exact[2 * i]) < n])
            p["missing"]["rows_with_missing"] = None
            p["duplicates"].update(rows=None, share=None, conflicting_labels=None)
            p["warnings"] = [w for w in p["warnings"] if "duplicates" not in w and "no label" not in w]
            tgt = p["target"]
            if tgt and p.get("task") != "regression":
                cnt = con.execute(f"SELECT {q(tgt)} AS v, count(*) FROM {src} WHERE {q(tgt)} IS NOT NULL"
                                  " GROUP BY 1 ORDER BY 2 DESC, 1").fetchall()
                tot = sum(r[1] for r in cnt) or 1
                # a class is named as the fit sees it (engine_v1: an int label with missing cells is float, '1.0')
                ty = df[tgt].dtype
                name = (lambda v: str(float(v))) if ty.kind == "f" else (lambda v: str(int(v))) if ty.kind in "iu" else str
                p["classes"] = [{"name": name(v), "count": int(k), "share": round(k / tot, 6)} for v, k in cnt]
                p["n_classes"] = len(p["classes"])
                counts = [c["count"] for c in p["classes"]]
                p["imbalance_ratio"] = round(max(counts) / min(counts), 4) if counts else None
                p["warnings"] = [w for w in p["warnings"] if not w.startswith("Class '")]
                if counts and min(counts) / max(counts) < 0.2:
                    rare = p["classes"][-1]
                    p["warnings"].insert(0, f"Class '{rare['name']}' is rare ({rare['share'] * 100:.1f}% of rows).")
                n_unl = n - tot
                if n_unl:
                    p["warnings"].append(f"{n_unl} rows have no label and will be dropped.")
            p["sampled"] = {"rows": int(len(df)), "of": n, "seed": 42,
                            "note": "types, examples and statistics from a seeded sample; rows, missing, distinct "
                                    "(approximate) and classes from the whole table"}
        return p

    def upload_version(self, parent_id: str, filename: str, content: bytes, *, by: str, row_key: str | None = None,
                       preview: bool = False, reason: str | None = None) -> dict[str, Any]:
        """DL1: a new file as the next version of `parent_id`. Rows are matched to the parent by `row_key` (a column
        unique in both files) or, without one, by an exact hash of the row over the columns both files share with an
        unchanged type. Matched rows keep their original row and their holdout / fold, so runs on the two versions
        pair on those rows; new rows get a seeded share of the holdout. Contract checks warn, never block."""
        from . import dataset_versions as DV

        parent = self.db.get_dataset(parent_id)
        if not parent:
            raise DatasetError(f"No dataset with id '{parent_id}'.")
        df, note, _ = read_upload(filename, content)
        kept = list(df.attrs.get("kept_as_text") or [])
        held = {c: df[c] for c in kept}
        df = _normalize_frame(df)
        for c, v in held.items():
            if c in df.columns:
                df[c] = v
        pdf = frames.read_version(parent_id)
        pschema = FIO.columns(self.schema(parent_id, pdf))
        # DL4 (m-e): a column the parent keeps as text stays text in the new file (its codes keep their zeros)
        schema = FIO.schema_of(df, pschema, {c: "text" for c in df.columns if pschema.get(c) == "text" or c in kept})
        df = FIO.roundtrip(df, schema)  # exactly what data.csv will read back as
        match = DV.match_rows(pdf, df, row_key)
        checks = DV.contract_checks(pdf, df, parent["target"])
        import hashlib

        sha = hashlib.sha256(FIO.to_csv_bytes(df, schema)).hexdigest()  # the bytes create_version will write
        same = self._same_bytes(sha, parent.get("root_id") or parent_id, schema)
        same = (same,) if same else None
        if same:
            checks["warnings"].append({"code": "identical_file", "message": f"This file has the same rows as {same[0]} (sha256 "
                                       f"{sha[:12]}…): the new version adds nothing but a pointer."})
        extra = {"kind": "upload_version", "filename": Path(filename).name, "matched_by": match["matched_by"],
                 "row_key": match.get("row_key"), "matched": match["matched"], "changed": match["changed"],
                 "added": match["added"], "removed": match["removed"], "checks": checks,
                 "columns_before": int(pdf.shape[1]), "columns_after": int(df.shape[1]),
                 **({"kept_as_text": kept} if kept else {})}
        if kept:
            checks.setdefault("notes", []).append(f"Kept as text (codes with leading zeros): {', '.join(kept)}.")
        changes = [{"op": "rows_changed", "n": match["changed"]}, {"op": "rows_added", "n": match["added"]},
                   {"op": "rows_removed", "n": match["removed"]}]
        changes = [c for c in changes if c["n"]] + [{"op": "column_added", "column": c} for c in checks["columns_added"]] + \
            [{"op": "column_removed", "column": c} for c in checks["columns_removed"]]
        n_ver = 1 + len(self.catalog.tree_ids(parent_id))
        if preview:  # DL2: what the upload would make, written nowhere
            return {"preview": True, "v": n_ver, "parent": parent_id, "filename": Path(filename).name, "rows": int(len(df)),
                    "n_cols": int(df.shape[1]), "sha256": sha, **{k: v for k, v in extra.items() if k != "kind"},
                    "would_be_default": not checks["warnings"],
                    "note": "nothing was written: send the file again without preview to create the version"}
        if reason:
            extra["reason"] = reason.strip()[:500]
        rec = self.derive(parent_id, df, None, "upload_version", changes, name=parent["name"],
                          note=f"{note} as v{n_ver} of {parent['name']}: {match['matched']:,} rows matched by "
                               f"{match['matched_by']}, {match['changed']:,} changed, {match['added']:,} new, {match['removed']:,} gone",
                          extra=extra, by=by, row_map=match["row_map"], new_holdout_share=config.HOLDOUT_SHARE, reason=reason,
                          schema=schema)
        rec["upload"] = {k: v for k, v in extra.items() if k != "kind"}
        return rec


    #: §5.5: a parent table re-hashed less than this long ago is trusted for an edit
    REHASH_HOURS = 24

    def verify_for_edit(self, parent_id: str, parent: dict[str, Any]) -> None:
        """§5.5: the parent's bytes are re-hashed before an edit carries them forward, unless every part was verified
        within REHASH_HOURS. 409 (DatasetConflict) when they no longer match what was committed."""
        from datetime import timedelta

        from .catalog.schema import audit, file_sha256

        if not frames.is_parquet(parent_id):
            stored, on_disk = parent.get("sha256"), file_sha256(self.data_file(parent_id))
            if stored and on_disk != stored:
                raise DatasetConflict(f"The data on disk differs from {parent_id} (stored sha256 {stored[:12]}…, file now "
                                      f"{(on_disk or 'missing')[:12]}…): it was changed outside the app, so it can't be edited. "
                                      "Restore the file, or create a new version from it (Data → the version → Create a new version "
                                      "from this file).")
            return
        lt = frames.lake_table(frames.table(parent_id))
        shas = [p["sha256"] for p in lt.parts]
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.REHASH_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
        q = ",".join("?" * len(shas))
        with self.db.connect() as con:
            fresh = con.execute(f"SELECT count(*) FROM blobs WHERE sha256 IN ({q}) AND verified_ok = 1 AND verified_at >= ?",
                                [*shas, cutoff]).fetchone()[0]
        if fresh == len(set(shas)):
            return
        now_sha, stored, bad = frames.hash_table(frames.table(parent_id))
        with self.db.connect() as con:
            if now_sha != stored:
                bad_shas = [shas[i] for i in bad]
                if bad_shas:
                    con.execute(f"UPDATE blobs SET verified_at = ?, verified_ok = 0 WHERE sha256 IN ({','.join('?' * len(bad_shas))})",
                                [now(), *bad_shas])
                audit(con, _actor_now(), "dataset_integrity_failed", {"dataset_id": parent_id, "table": lt.name, "stored": stored,
                                                                  "read": now_sha, "parts_differing": bad, "during": "edit"})
                raise DatasetConflict(f"Table {lt.name} of {parent_id} no longer has the bytes it was committed with (table_sha256 "
                                      f"{stored[:12]}… committed, {now_sha[:12]}… now; {len(bad)} of {len(shas)} file(s) differ): "
                                      "it can't be edited. Restore the file from a backup.")
            con.execute(f"UPDATE blobs SET verified_at = ?, verified_ok = 1 WHERE sha256 IN ({q})", [now(), *shas])

    def _edit_base(self, con: Any, pr: Any) -> None:
        """View _e0 on `con`: the parent table (`__row_id`, columns). A CSV-era parent is registered from its typed read,
        with its schema's kinds restored (an integer column with missing cells is BIGINT again, not float)."""
        from .lake import derive as LD

        if pr.storage == "parquet":
            con.execute(f"CREATE OR REPLACE TEMP VIEW _e0 AS SELECT * FROM ({pr.p.sql})")
            return
        pr.p.attach(con)
        sql, _ = LD.typed_select(con, "_parent_csv", pr.types)
        con.execute(f"CREATE OR REPLACE TEMP VIEW _e0 AS SELECT {sql} FROM _parent_csv")

    def _edit_compile(self, con: Any, pr: Any, parent: dict[str, Any], ops: Any, st_col: str | None, st_reason: str,
                      acknowledge: Any, by: str, parent_id: str) -> tuple[Any, dict[str, Any]]:
        """Compile the ops over _e0 and check the result: DL4 target guards, set_target, the one-class block (§4.4),
        at least two rows and columns, and the summary (= the diff against the parent, on __row_id)."""
        from . import dataset_edit as DE
        from .catalog.schema import audit
        from .dataset_versions import Matcher, type_family

        self._edit_base(con, pr)
        old_t = parent["target"]
        try:
            comp = DE.compile_ops(con, "_e0", ops, target=old_t, next_row_id=pr.next_row_id)
        except DE.EditError as e:
            raise DatasetError(str(e)) from e
        if comp.target_touched and st_col is None:  # DL4 (M-c): never re-target silently
            t = comp.target_touched
            msg = (f"Operation {t['i']} {t['what']} the target '{old_t}'. Runs on this version would predict something else, so "
                   "this is refused. Leave the target alone, or say what the new version predicts: send "
                   '"set_target": {"column": "<column>", "reason": "<why the target changes>"} with the edit.')
            with self.db.connect() as con2:
                audit(con2, by, "dataset_edit_refused", comment=msg[:500],
                      details={"dataset_id": parent_id, "root_id": parent.get("root_id") or parent_id, "status": 400,
                               "target": old_t, "op": t})
            raise DatasetError(msg)
        v = comp.view
        names = [n for n, _ in comp.cols]
        ptypes = {r[0]: r[1] for r in con.execute("DESCRIBE SELECT * FROM _e0").fetchall() if r[0] != "__row_id"}
        if st_col is not None and st_col not in names:
            raise DatasetError(f"set_target: no column {st_col!r} in the edited version (columns: {', '.join(names[:12])}).")
        n0 = int(con.execute("SELECT count(*) FROM _e0").fetchone()[0])
        n1 = int(con.execute(f"SELECT count(*) FROM {v}").fetchone()[0])
        if len(names) < 2 or n1 < 2:
            raise DatasetError("A version needs at least two rows and two columns.")
        if st_col is not None:
            k, nn = con.execute(f"SELECT count(DISTINCT {DE.q(st_col)}), count({DE.q(st_col)}) FROM {v}").fetchone()
            if not nn:
                raise DatasetError(f"Column '{st_col}' is empty: every row is missing, so there is nothing to learn. "
                                   "Pick the column that holds the outcome.")
            if k < 2:
                one = con.execute(f"SELECT {DE.q(st_col)}::VARCHAR FROM {v} WHERE {DE.q(st_col)} IS NOT NULL LIMIT 1").fetchone()[0]
                raise DatasetError(f"Column '{st_col}' has one value ({one!r}) in all {nn:,} labelled rows: a model needs "
                                   "at least two outcomes to learn from. Add rows with the other outcome.")
        one_class = None
        if st_col is None and old_t and old_t in ptypes and comp.target == old_t:  # §4.4: blocked unless acknowledged
            nb = con.execute(f"SELECT count(DISTINCT {DE.q(old_t)}) FROM _e0").fetchone()[0]
            na, left = con.execute(f"SELECT count(DISTINCT {DE.q(old_t)}), min({DE.q(old_t)})::VARCHAR FROM {v}").fetchone()
            one_class = guard_one_class_counts(int(nb), int(na), [left] if left is not None else [], old_t, acknowledge)
        inv = {new: old for old, new in comp.renamed.items()}
        # the diff on __row_id: matched / changed (a shared column of the same type family differs) / added / removed
        shared = [(n, inv.get(n, n)) for n, t in comp.cols
                  if inv.get(n, n) in ptypes and type_family(ptypes[inv.get(n, n)]) == type_family(t)]
        diffx = " OR ".join(f"a.{DE.q(o)} IS DISTINCT FROM b.{DE.q(n)}" for n, o in shared) or "false"
        matched, changed = con.execute(f"SELECT count(*), count(*) FILTER (WHERE {diffx}) FROM _e0 a JOIN {v} b USING (__row_id)").fetchone()
        sel = ", ".join(f"{DE.q(n)} AS {DE.q(inv.get(n, n))}" for n in names)
        mt = Matcher(con, "SELECT * FROM _e0", f"SELECT __row_id AS __fr, {sel} FROM {v}")
        checks = mt.contract(old_t if st_col is None or st_col == old_t else None)
        checks = {k: checks[k] for k in ("warnings", "columns_added", "columns_removed", "type_changes", "rows_before",
                                         "rows_after", "renamed_maybe")}
        for old, new in comp.renamed.items():  # a rename is not a loss: say so (the version is still not the default)
            if old in ptypes and new in names:
                checks["warnings"].append({"code": "column_removed", "column": old,
                                           "message": f"'{old}' was renamed to '{new}': runs that used '{old}' can't be re-fitted on this version as they were."})
        if comp.renamed:
            checks["columns_removed"] = sorted(set(checks["columns_removed"]) | {o for o, n in comp.renamed.items() if o in ptypes and n in names})
            checks["columns_added"] = sorted(set(checks["columns_added"]) | {n for o, n in comp.renamed.items() if n in names and o in ptypes})
        if one_class:
            checks["warnings"].append(one_class)
        if st_col is not None and st_col != old_t:
            checks["warnings"].append({"code": "target_changed", "column": st_col,
                                       "message": f"The target changes from '{old_t}' to '{st_col}' ({st_reason}): runs on this version "
                                                  "predict something else, and are not compared with runs on the old target."})
        kind = lambda t: DE.KIND_OF.get(DE.vtype(t), "text")  # noqa: E731
        schema = {n: kind(t) for n, t in comp.cols}
        types_changed = [{"column": n, "from": kind(ptypes[inv.get(n, n)]), "to": kind(t)} for n, t in comp.cols
                         if inv.get(n, n) in ptypes and kind(ptypes[inv.get(n, n)]) != kind(t)]
        new_t = st_col if st_col is not None else comp.target
        summary = {"matched": int(matched), "changed": int(changed), "added": n1 - int(matched), "removed": n0 - int(matched),
                   "columns_before": len(ptypes), "columns_after": len(names), "checks": checks,
                   "renamed": comp.renamed, "edit_log": comp.log, "ops_summary": [e["summary"] for e in comp.log],
                   "n_ops": len(comp.log), "schema": schema, "types_changed": types_changed,
                   "target": {"before": old_t, "after": new_t, **({"set_by_edit": True, "reason": st_reason} if st_col is not None else {})}}
        return comp, summary

    def edit_version(self, parent_id: str, ops: Any, *, by: str, reason: str, preview: bool = False,
                     set_target: Any = None, acknowledge: Any = None) -> dict[str, Any]:
        """DL3 / P07 (§5.5): typed edits (dataset_edit.OPS) compiled into one DuckDB query over the parent table and
        committed as a NEW version through the VersionWriter: edited rows keep their `__row_id` (and so their holdout /
        fold), added rows get new ids (and a seeded share of the holdout), deleted rows are gone. Only the edited table is
        rewritten; the parent's other tables are carried. The version edited is never changed. `preview` writes nothing.
        The parent's bytes are re-hashed first (409 when they changed outside the app).

        DL4: column types are kept (an int stays int, text stays text; a cast changes the schema). An op that drops,
        renames or casts the target is refused unless `set_target` {column, reason} names the target of the new version
        explicitly (audited either way). §4.4: an edit that leaves the target with one class is blocked (400
        target_one_class) unless acknowledged. Repeating an edit returns the existing version (dedupe by data)."""
        from . import dataset_edit as DE
        from .catalog.schema import add_edge, audit
        from .lake import derive as LD
        from .lake import duck
        from .lake.store import open_store
        from .lake.writer import LakeError, VersionWriter

        parent = self.db.get_dataset(parent_id)
        if not parent:
            raise DatasetError(f"No dataset with id '{parent_id}'.")
        st_col, st_reason = None, ""
        if set_target is not None:
            if not isinstance(set_target, dict) or not isinstance(set_target.get("column"), str) or not set_target["column"].strip():
                raise DatasetError('set_target is {"column": <the new version\'s target>, "reason": "…"}.')
            st_col, st_reason = set_target["column"].strip(), str(set_target.get("reason") or "").strip()
            if len(st_reason) < 10:
                raise DatasetError("Changing the target needs its own reason (set_target.reason, at least 10 characters): "
                                   "models trained on the old target can't be compared with ones trained on the new.")
        self.verify_for_edit(parent_id, parent)
        lake = open_store(self.settings)
        try:
            pr = LD.ParentRows(self.db, lake, self, parent_id)
        except LakeError as e:
            raise DatasetError(e.message) from e
        n_ver = 1 + len(self.catalog.tree_ids(parent_id))
        if preview:
            with duck.job() as con:
                comp, summary = self._edit_compile(con, pr, parent, ops, st_col, st_reason, acknowledge, by, parent_id)
                cols = [n for n, _ in comp.cols]
                rows = con.execute(f"SELECT * EXCLUDE (__row_id) FROM {comp.view} ORDER BY __row_id LIMIT 5").fetchall()
                n1 = int(con.execute(f"SELECT count(*) FROM {comp.view}").fetchone()[0])
            sample = [{c: DE._jsonable(x) for c, x in zip(cols, r)} for r in rows]
            return {"preview": True, "v": n_ver, "parent": parent_id, "rows": n1, "n_cols": len(cols), "sha256": None,
                    **summary, "would_be_default": not summary["checks"]["warnings"],
                    "columns": cols, "sample": sample,
                    "note": "nothing was written: send the same operations without preview to create the version"}
        try:
            with VersionWriter(self.db, lake, op="edit", by=by, parent_id=parent_id, reason=reason, name=parent["name"],
                               primary_table=pr.table, dedupe=True) as w:
                comp, summary = self._edit_compile(w.con, pr, parent, ops, st_col, st_reason, acknowledge, by, parent_id)
                w.write(pr.table, f"SELECT * FROM {comp.view}")
                names = [n for n, _ in comp.cols]
                LD.carry_rest(w, pr, self.db, pr.table, names)
                rows = w.con.execute(f"SELECT __row_id FROM {comp.view} ORDER BY __row_id").fetchnumpy()["__row_id"].astype("int64")
                w.extra_ops.append(LD.no_floats(
                    {"op": "edit", "table": pr.table, "n_ops": len(comp.log), "ops_summary": summary["ops_summary"][:50],
                     "rows": {k: summary[k] for k in ("matched", "changed", "added", "removed")},
                     "renamed": comp.renamed, "types_changed": summary["types_changed"], "target": summary["target"],
                     "next_row_id": int(pr.next_row_id)}))
                w.warnings = LD.no_floats([{k: v for k, v in x.items() if k not in ("values", "examples")} for x in summary["checks"]["warnings"]])
                new_t = summary["target"]["after"]
                # P09: the parent's split carried by join on __row_id (a split for another target is drawn on first use)
                split_rec = LD.split_into(w, pr, pr.table, rows, None, config.HOLDOUT_SHARE,
                                          target_in_child=new_t == parent["target"])
                if split_rec:
                    w.extra_ops.append(LD.no_floats({"op": "split_carried", "table": pr.table, **split_rec}))
                out = w.commit(make_default=False, target=new_t,
                               note=f"Edited {parent_id} as v{n_ver}: " + "; ".join(summary["ops_summary"][:4])
                                    + ("…" if len(summary["ops_summary"]) > 4 else ""))
        except LakeError as e:
            raise DatasetError(e.message) from e
        vid = out["version_id"]
        root = pr.root
        if out.get("deduped"):
            with self.db.connect() as con:
                audit(con, by, "dataset_version_deduplicated", comment=reason,
                      details={"dataset_id": vid, "root_id": root, "parent": parent_id, "op": "edit", "data_sha256": out["data_sha256"],
                               "note": "the same data as an existing version: no new version was made, the existing one is returned"})
            rec = dict(self.db.get_dataset(vid) or {})
            summary["duplicate_of"] = vid
            return {**rec, "created": False, "deduplicated": {"existing": vid, "data_sha256": out["data_sha256"]},
                    "edit": {k: v for k, v in summary.items() if k != "edit_log"}}
        changes = [{"op": e["op"], "n": e.get("cells") or e.get("rows") or 0, **({"column": e["column"]} if e.get("column") else {})}
                   for e in comp.log]
        lineage = {"kind": "edit", "matched_by": "edit (rows keep their __row_id)", **summary, "parent": parent_id, "root": root,
                   "op": "edit", "rows_before": len(pr.ids), "rows_after": int(len(rows)), "changes": changes, "by": by,
                   **({"reason": reason} if reason else {})}
        LD.lineage_summary(self, vid, lineage)  # P09: no row maps (the rows carry __row_id)
        (self.dir(vid) / "edit_ops.json").write_text(json.dumps({"parent": parent_id, "by": by, "reason": reason, "ops": ops,
                                                                 **({"set_target": set_target} if set_target else {})}, default=str))
        rec = self.finish_lake_version(vid, target=new_t, by=by)
        profile = rec["profile"]
        profile["target_source"] = "confirmed" if st_col is not None else parent["profile"].get("target_source", "given")
        profile["lineage"] = {k: v for k, v in lineage.items() if k not in ("changes", "edit_log")}
        rec = {**rec, "note": out.get("note") or rec.get("note"), "profile": profile}
        self.db.insert_dataset({k: rec[k] for k in ("id", "name", "filename", "source", "note", "created_at", "size_bytes",
                                                     "rows", "target", "kind", "profile")})
        with self.db.connect() as con:
            add_edge(con, ("dataset", parent_id), ("dataset", vid), "derived_from", actor=by, update_meta=True,
                     meta={"op": "edit", "storage": "parquet", "rows_before": len(pr.ids), "rows_after": int(len(rows)),
                           "ops_summary": summary["ops_summary"][:20], "n_changes": len(changes), **({"reason": reason} if reason else {})})
            if st_col is not None:
                audit(con, by, "dataset_target_changed", comment=st_reason[:500],
                      details={"dataset_id": vid, "root_id": root, "parent": parent_id, "from": parent["target"], "to": st_col,
                               "edit_reason": reason})
        rec = self.db.get_dataset(vid) or rec
        rec["edit"] = summary
        rec["created"] = True
        return rec

    def create_from_frame(self, df: pd.DataFrame, *, name: str, filename: str, note: str, target: str | None = None,
                          lineage: dict[str, Any] | None = None, by: str | None = None) -> dict[str, Any]:
        """A new dataset from a frame (an upload, or G9's SQL query). `lineage` (G9: the query, the redacted URL, the
        version) goes to lineage.json and the profile, like Autopilot's derived versions."""
        df.columns = [str(c).strip() or f"col_{i + 1}" for i, c in enumerate(df.columns)]
        if df.columns.duplicated().any():
            raise DatasetError("Column names must be unique.")
        if df.shape[1] < 2:
            raise DatasetError("A dataset needs at least two columns.")
        if len(df) < 2:
            raise DatasetError("A dataset needs at least two rows.")
        from .profiling import coerce_numeric

        kept = list(df.attrs.get("kept_as_text") or [])
        held = {c: df[c] for c in kept if c in df.columns}
        df, coerced = coerce_numeric(df)  # A1 #5: '  ' / 'n/a' / '$12,000' in a number column must not make it a category or an ID
        dataset_id = "ds_" + secrets.token_hex(4)
        d = self.dir(dataset_id)
        d.mkdir(parents=True, exist_ok=True)
        for c, v in held.items():  # DL4 (m-e): codes with leading zeros are not numbers
            df[c] = v
        coerced = [x for x in coerced if x.get("column") not in held]
        schema = FIO.schema_of(df, force={c: "text" for c in kept})  # DL4 (m-e): types travel with the version
        self.data_file(dataset_id).write_bytes(FIO.to_csv_bytes(df, schema))
        FIO.save(d, schema)
        if coerced:
            (d / "coerced.json").write_text(json.dumps(coerced))
        df = frames.read_version(dataset_id)  # profile exactly what the engine will read back
        try:
            rec = self._register(dataset_id, df, name=name, filename=filename, source="upload", note=note,
                                 size_bytes=self.data_file(dataset_id).stat().st_size, target=target, by=by)
        except DatasetError:
            shutil.rmtree(d, ignore_errors=True)
            raise
        if kept:  # DL4 (m-e): say which columns were kept as text, and why
            rec["profile"]["kept_as_text"] = kept
            rec["profile"].setdefault("warnings", []).append(
                f"Kept as text (codes with leading zeros, e.g. ZIP or ID codes): {', '.join(kept)}. Cast to number in Data → Edit if they are amounts.")
            self.db.insert_dataset(rec)
        if lineage:
            lineage = {**lineage, "root": lineage.get("root") or dataset_id}
            (d / "lineage.json").write_text(json.dumps(lineage))
            rec["profile"]["lineage"] = lineage
            self.db.insert_dataset(rec)
            if lineage.get("parent") and self.db.get_dataset(lineage["parent"]):  # a SQL refresh: a version of new rows
                self.catalog.record_version(lineage["parent"], rec, self.data_file(dataset_id), op=str(lineage.get("source") or "refresh"),
                                            meta={"version": lineage.get("version")}, paired=False)
        return rec
